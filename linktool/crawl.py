"""Crawler: robots.txt, sitemaps, Crawl-delay, canonicals, link capture."""
import gzip
import io
import re
import threading
import time
import urllib.robotparser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import requests
from bs4 import BeautifulSoup

USER_AGENT = "PersonalLinkAudit/1.0 (+private internal-link audit of my own site)"
ROBOTS_AGENT = "PersonalLinkAudit"

SKIP_EXT = re.compile(
    r"\.(jpe?g|png|gif|webp|avif|svg|ico|bmp|tiff?|pdf|zip|gz|rar|7z|mp4|mov|avi|webm|"
    r"mp3|wav|ogg|css|js|json|xml|txt|rss|atom|woff2?|ttf|eot|docx?|xlsx?|pptx?|csv)$",
    re.I,
)
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|_ga$|ref$|replytocom$|share$|amp$)", re.I)

# Places inside the article body that are really navigation, not editorial links.
NON_CONTEXTUAL = (
    "nav, aside, footer, header, form, script, style, noscript, figure.wp-block-embed, "
    "[class*=related], [id*=related], [class*=share], [class*=social], [class*=comment], "
    "[id*=comment], [class*=breadcrumb], [class*=author-box], [class*=authorbox], "
    "[class*=newsletter], [class*=subscribe], [class*=table-of-contents], [class*=ez-toc], "
    "[id*=ez-toc], [class*=lwptoc], [class*=yarpp], [class*=jp-relatedposts], "
    "[class*=wp-block-latest-posts], [class*=post-navigation], [class*=nav-links], "
    "[class*=pin-it], [class*=sidebar], [class*=widget], [class*=adthrive], [class*=mv-ad], "
    "[class*=raptive], [class*=sticky]"
)
CONTENT_SELECTORS = [
    ".entry-content", ".post-content", ".article-content", ".single-content",
    "[itemprop=articleBody]", ".td-post-content", ".content-area article", "article",
    "main", "#content", ".content", "#main",
]
BLOCK_TAGS = ["p", "li", "h2", "h3", "h4", "h5", "h6", "blockquote", "td", "dd", "figcaption"]
SENT_SPLIT = re.compile(r"(?<=[.!?])[\"”’)]?\s+(?=[A-Z0-9\"“‘(])")


def host_key(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalise(url, base=None):
    """Return a cleaned absolute URL, or None if it isn't a crawlable web page."""
    if not url:
        return None
    url = url.strip()
    if url.startswith(("mailto:", "tel:", "javascript:", "data:", "#", "sms:", "whatsapp:")):
        return None
    if base:
        url = urljoin(base, url)
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return None
    if SKIP_EXT.search(p.path or ""):
        return None
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    path = re.sub(r"/{2,}", "/", p.path or "/")
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", urlencode(q), ""))


def page_key(url):
    """Identity for a page that ignores http/https, www and trailing slashes."""
    p = urlparse(url)
    path = p.path.rstrip("/") or "/"
    return host_key(p.netloc) + path + (("?" + p.query) if p.query else "")


class Crawler:
    def __init__(self, start_url, max_pages=0, workers=4, delay=0.25, log=print,
                 follow_links=False, timeout=25):
        start = start_url.strip()
        if not start.startswith("http"):
            start = "https://" + start
        self.start = normalise(start) or start
        self.site_host = host_key(urlparse(self.start).netloc)
        self.root = f"{urlparse(self.start).scheme}://{urlparse(self.start).netloc}"
        self.max_pages = max_pages or 10**9
        self.workers = max(1, workers)
        self.base_delay = delay
        self.log = log
        self.follow_links = follow_links
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-GB,en;q=0.8",
        })
        adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=2)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.robots = urllib.robotparser.RobotFileParser()
        self.crawl_delay = None
        self.pages = {}          # key -> page dict
        self.aliases = {}        # key -> canonical key
        self.errors = []
        self.skipped_robots = []
        self.sitemaps_used = []
        self.discovery = "sitemap"
        self._lock = threading.Lock()
        self._next_slot = 0.0

    # ---------- politeness ----------
    def _wait_turn(self):
        gap = self.crawl_delay if self.crawl_delay is not None else self.base_delay
        if gap <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + gap
        pause = slot - time.monotonic()
        if pause > 0:
            time.sleep(pause)

    def same_site(self, url):
        return host_key(urlparse(url).netloc) == self.site_host

    def allowed(self, url):
        try:
            return self.robots.can_fetch(ROBOTS_AGENT, url)
        except Exception:
            return True

    # ---------- robots + sitemaps ----------
    def load_robots(self):
        robots_url = self.root + "/robots.txt"
        sitemaps = []
        try:
            r = self.session.get(robots_url, timeout=self.timeout)
            text = r.text if r.status_code == 200 else ""
        except requests.RequestException as e:
            self.log(f"robots.txt not reachable ({e}); assuming everything is allowed")
            text = ""
        self.robots.parse(text.splitlines())
        cd = self._crawl_delay(text)
        if cd is not None:
            self.crawl_delay = float(cd)
            self.workers = 1
            self.log(f"robots.txt asks for Crawl-delay {cd}s — crawling one page at a time")
        for line in text.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemaps.append(line.split(":", 1)[1].strip())
        return sitemaps

    @staticmethod
    def _crawl_delay(text):
        """Crawl-delay for our agent, else for '*'. Handles decimals like 0.5,
        which Python's robotparser ignores."""
        groups, agents, in_rules = {}, [], False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = (x.strip() for x in line.split(":", 1))
            k = k.lower()
            if k == "user-agent":
                if in_rules:
                    agents, in_rules = [], False
                agents.append(v.lower())
            else:
                in_rules = True
                if k == "crawl-delay":
                    try:
                        for a in agents:
                            groups[a] = float(v)
                    except ValueError:
                        pass
        for a, d in groups.items():
            if a != "*" and a in ROBOTS_AGENT.lower():
                return d
        return groups.get("*")

    def _fetch_xml(self, url):
        try:
            r = self.session.get(url, timeout=self.timeout)
        except requests.RequestException:
            return None
        if r.status_code != 200:
            return None
        data = r.content
        if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
            try:
                data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
            except OSError:
                pass
        if b"<urlset" not in data[:2000] and b"<sitemapindex" not in data[:2000]:
            return None
        return BeautifulSoup(data, "xml")

    def collect_sitemap_urls(self, declared):
        candidates = list(declared) or [
            self.root + p for p in ("/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml",
                                    "/post-sitemap.xml", "/sitemap-index.xml")
        ]
        files, seen_maps, queue = [], set(), deque(candidates)
        while queue and len(seen_maps) < 500:
            sm = queue.popleft()
            if sm in seen_maps:
                continue
            seen_maps.add(sm)
            soup = self._fetch_xml(sm)
            if soup is None:
                continue
            if soup.find("sitemapindex"):
                for loc in soup.select("sitemap > loc"):
                    queue.append(loc.get_text(strip=True))
                continue
            urls = []
            for loc in soup.select("url > loc"):
                u = normalise(loc.get_text(strip=True))
                if u and self.same_site(u):
                    urls.append(u)
            if urls:
                files.append((sm, urls))
            if not declared and files:
                break  # first guessed location that works is enough
        self.sitemaps_used = [f for f, _ in files]
        # Round-robin across sitemap files so every section is sampled.
        ordered, seen = [], set()
        iters = [iter(u) for _, u in files]
        while iters:
            nxt = []
            for it in iters:
                u = next(it, None)
                if u is None:
                    continue
                nxt.append(it)
                k = page_key(u)
                if k not in seen:
                    seen.add(k)
                    ordered.append(u)
            iters = nxt
        return ordered

    # ---------- page fetch + parse ----------
    def fetch(self, url):
        self._wait_turn()
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        except requests.RequestException as e:
            return {"url": url, "error": str(e)[:200]}
        ctype = r.headers.get("Content-Type", "")
        if r.status_code != 200:
            return {"url": url, "status": r.status_code, "error": f"HTTP {r.status_code}"}
        if "html" not in ctype:
            return {"url": url, "status": r.status_code, "error": f"not HTML ({ctype[:40]})"}
        r.encoding = r.encoding or r.apparent_encoding
        return {"url": url, "final_url": normalise(r.url) or url, "status": 200, "html": r.text}

    def parse(self, url, html):
        soup = BeautifulSoup(html, "lxml")
        page = {"url": url, "key": page_key(url)}
        page["title"] = (soup.title.get_text(" ", strip=True) if soup.title else "")[:300]
        h1 = soup.find("h1")
        page["h1"] = h1.get_text(" ", strip=True)[:300] if h1 else ""
        md = soup.find("meta", attrs={"name": "description"})
        page["description"] = (md.get("content") or "")[:400] if md else ""
        robots_meta = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
        page["noindex"] = bool(robots_meta and "noindex" in (robots_meta.get("content") or "").lower())
        canon = soup.find("link", rel=lambda v: v and "canonical" in (v if isinstance(v, list) else [v]))
        page["canonical"] = normalise(canon.get("href"), url) if canon and canon.get("href") else None
        pub = soup.find("meta", attrs={"property": "article:published_time"})
        page["published"] = (pub.get("content") or "")[:25] if pub else ""
        mod = soup.find("meta", attrs={"property": "article:modified_time"})
        page["modified"] = (mod.get("content") or "")[:25] if mod else ""

        types = set()
        for s in soup.find_all("script", type="application/ld+json"):
            for t in re.findall(r'"@type"\s*:\s*(\[[^\]]*\]|"[^"]+")', s.string or s.get_text() or ""):
                for name in re.findall(r'"([^"]+)"', t):
                    types.add(name)
        page["schema_types"] = sorted(types)[:20]
        page["has_tool"] = bool(soup.select("form input[type=number], form select, input[type=range], canvas"))

        # Body links of the whole page (before we cut things out) for nav/total counts.
        all_links, keep_alive = [], []
        for a in soup.find_all("a", href=True):
            keep_alive.append(a)  # keeps id() unique while we cut junk out below
            u = normalise(a["href"], url)
            if u and self.same_site(u):
                text = a.get_text(" ", strip=True)
                if not text:
                    img = a.find("img")
                    text = ("[image] " + (img.get("alt") or "").strip()) if img else ""
                all_links.append((id(a), u, text[:200], " ".join(a.get("rel") or [])))

        # Find the editorial content area.
        root = None
        for sel in CONTENT_SELECTORS:
            for cand in soup.select(sel):
                if len(cand.get_text(" ", strip=True).split()) >= 120:
                    root = cand
                    break
            if root is not None:
                break
        if root is None:
            root = soup.body or soup
        for junk in root.select(NON_CONTEXTUAL):
            junk.decompose()
        ctx_anchor_ids = {id(a) for a in root.find_all("a", href=True)}

        links = []
        for aid, u, text, rel in all_links:
            links.append({
                "to": u, "to_key": page_key(u), "anchor": text,
                "ctx": aid in ctx_anchor_ids, "rel": rel,
            })
        page["links"] = links
        if self.follow_links:
            page["_discovered"] = [u for _, u, _, _ in all_links]

        # Sentences and headings from the content area.
        sentences, words = [], 0
        seen_text = set()
        for el in root.find_all(BLOCK_TAGS):
            if el.find(BLOCK_TAGS):  # nested container (e.g. li with p inside) – handled by child
                continue
            txt = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if not txt or txt in seen_text:
                continue
            seen_text.add(txt)
            words += len(txt.split())
            if el.name in ("h2", "h3", "h4", "h5", "h6"):
                sentences.append({"t": txt, "h": 1, "a": []})
                continue
            anchors = [a.get_text(" ", strip=True) for a in el.find_all("a")]
            anchors = [x for x in anchors if x]
            for s in SENT_SPLIT.split(txt):
                s = s.strip()
                if len(s.split()) < 4:
                    continue
                sentences.append({"t": s, "h": 0, "a": [x for x in anchors if x in s]})
        page["sentences"] = sentences
        page["word_count"] = words
        return page

    # ---------- main loop ----------
    def process(self, url):
        res = self.fetch(url)
        if "html" not in res:
            return res
        final = res["final_url"]
        if not self.same_site(final):
            return {"url": url, "error": "redirects off-site"}
        try:
            page = self.parse(final, res["html"])
        except Exception as e:  # never let one weird page kill the run
            return {"url": url, "error": f"parse failed: {e}"[:200]}
        page["requested"] = url
        return page

    def run(self):
        declared = self.load_robots()
        queue = self.collect_sitemap_urls(declared)
        if queue:
            self.log(f"Found {len(queue)} URLs in {len(self.sitemaps_used)} sitemap file(s)")
        else:
            self.discovery = "links"
            self.follow_links = True
            queue = [self.start]
            self.log("No sitemap found — following links from the home page")

        pending = deque(queue)
        queued = {page_key(u) for u in queue}
        done = 0
        started = time.time()
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            inflight = set()
            while (pending or inflight) and len(self.pages) < self.max_pages:
                while pending and len(inflight) < self.workers * 2 and \
                        len(self.pages) + len(inflight) < self.max_pages:
                    u = pending.popleft()
                    if not self.allowed(u):
                        self.skipped_robots.append(u)
                        continue
                    inflight.add(pool.submit(self.process, u))
                if not inflight:
                    break
                finished = [f for f in inflight if f.done()]
                if not finished:
                    time.sleep(0.05)
                    continue
                for f in finished:
                    inflight.discard(f)
                    res = f.result()
                    done += 1
                    if "error" in res:
                        self.errors.append({"url": res["url"], "error": res["error"]})
                        continue
                    key = res["key"]
                    canon = res.get("canonical")
                    if canon and self.same_site(canon) and page_key(canon) != key:
                        ck = page_key(canon)
                        self.aliases[key] = ck
                        req_key = page_key(res.get("requested", res["url"]))
                        if req_key != key:
                            self.aliases[req_key] = ck
                        if ck not in queued:
                            queued.add(ck)
                            pending.appendleft(canon)
                        continue
                    req_key = page_key(res.get("requested", res["url"]))
                    if req_key != key:
                        self.aliases[req_key] = key  # redirected
                    if key in self.pages:
                        continue
                    discovered = res.pop("_discovered", [])
                    self.pages[key] = res
                    if self.follow_links:
                        for u in discovered:
                            k = page_key(u)
                            if k not in queued:
                                queued.add(k)
                                pending.append(u)
                    if done % 50 == 0:
                        rate = done / max(1, time.time() - started)
                        self.log(f"  fetched {done} URLs, {len(self.pages)} pages kept ({rate:.1f}/s)")
        self.log(f"Crawl finished: {len(self.pages)} pages, {len(self.errors)} errors, "
                 f"{len(self.skipped_robots)} blocked by robots.txt")
        return self
