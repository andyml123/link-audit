"""Find and read sitemaps.

Give it either a sitemap address (https://site.com/sitemap_index.xml) or just a
site (site.com). For a site it checks robots.txt, then the usual sitemap
locations, then the home page's <link rel="sitemap">. Sitemap indexes are
followed all the way down; .gz and plain-text sitemaps work too.
"""
import gzip
import io
import re
from collections import OrderedDict
from urllib.parse import urljoin, urlparse

import requests
from lxml import etree

OWN_AGENT = "SitemapExtractor/1.0 (+personal sitemap reader)"
BROWSER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.5 Safari/605.1.15")
COMMON_PATHS = (
    "/sitemap_index.xml", "/sitemap.xml", "/wp-sitemap.xml", "/sitemap-index.xml",
    "/sitemaps.xml", "/sitemap/sitemap.xml", "/sitemap.xml.gz", "/sitemap_index.xml.gz",
    "/post-sitemap.xml", "/page-sitemap.xml", "/sitemap.txt",
)
MAX_FILES = 1000


def looks_like_sitemap(address):
    path = urlparse(address).path.lower()
    return bool(path) and path != "/" and (
        "sitemap" in path or path.endswith((".xml", ".xml.gz", ".gz", ".txt")))


def with_scheme(address):
    address = address.strip()
    if not re.match(r"^https?://", address, re.I):
        address = "https://" + address.lstrip("/")
    return address


def local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


class Extractor:
    def __init__(self, log=print, timeout=30):
        self.log = log
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": OWN_AGENT,
                                     "Accept": "application/xml,text/xml,text/plain,text/html;q=0.8,*/*;q=0.5"})
        self.checked = []      # (address, result) for the "what was tried" list
        self.files = []        # dicts: url, kind, count / children
        self.urls = OrderedDict()  # loc -> {"lastmod", "sitemap"}
        self.problems = []

    # ---------- fetching ----------
    def get(self, url):
        """GET with one retry as a normal browser if the site refuses our agent."""
        for agent in (None, BROWSER_AGENT):
            headers = {"User-Agent": agent} if agent else {}
            try:
                r = self.session.get(url, timeout=self.timeout, headers=headers, allow_redirects=True)
            except requests.RequestException as e:
                return None, f"couldn't connect ({e.__class__.__name__})"
            if r.status_code in (401, 403, 406, 429, 503) and agent is None:
                continue
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}"
            return r, None
        return None, f"HTTP {r.status_code} (blocked)"

    def fetch_sitemap(self, url):
        r, err = self.get(url)
        if r is None:
            return None, None, err
        data = r.content
        if data[:2] == b"\x1f\x8b":
            try:
                data = gzip.GzipFile(fileobj=io.BytesIO(data)).read()
            except OSError:
                return None, None, "broken .gz file"
        head = data[:3000].lstrip().lower()
        if b"<urlset" in head or b"<sitemapindex" in head:
            parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
            try:
                root = etree.fromstring(data, parser)
            except etree.XMLSyntaxError:
                root = None
            if root is None:
                return None, None, "XML couldn't be read"
            return "xml", root, None
        if head.startswith(b"<"):
            return None, None, "not a sitemap (got a web page or other XML)"
        lines = [l.strip() for l in data.decode("utf-8", "replace").splitlines()]
        lines = [l for l in lines if re.match(r"^https?://\S+$", l)]
        if lines:
            return "txt", lines, None
        return None, None, "not a sitemap"

    # ---------- discovery ----------
    def discover(self, site):
        p = urlparse(site)
        root = f"{p.scheme}://{p.netloc}"
        found = []
        r, err = self.get(root + "/robots.txt")
        if r is not None:
            for line in r.text.splitlines():
                line = line.split("#", 1)[0].strip()
                if line.lower().startswith("sitemap:"):
                    sm = urljoin(root + "/", line.split(":", 1)[1].strip())
                    if sm and sm not in found:
                        found.append(sm)
            self.checked.append((root + "/robots.txt",
                                 f"lists {len(found)} sitemap(s)" if found else "no Sitemap: lines"))
        else:
            self.checked.append((root + "/robots.txt", err))
        if found:
            return found
        for path in COMMON_PATHS:
            kind, _, err = self.fetch_sitemap(root + path)
            self.checked.append((root + path, "found" if kind else err))
            if kind:
                return [root + path]
        r, err = self.get(root + "/")
        if r is not None:
            for m in re.finditer(r"<link\b[^>]*>", r.text[:200000], re.I):
                tag = m.group(0)
                if re.search(r"rel=[\"']?sitemap", tag, re.I):
                    href = re.search(r"href=[\"']([^\"']+)", tag, re.I)
                    if href:
                        found.append(urljoin(root + "/", href.group(1)))
            self.checked.append((root + "/", f"home page links {len(found)} sitemap(s)"
                                 if found else "no sitemap link in home page"))
        else:
            self.checked.append((root + "/", err))
        return found

    # ---------- reading ----------
    def read_all(self, starts):
        queue, seen = [(s, None) for s in starts], set()
        while queue:
            url, parent = queue.pop(0)
            if url in seen:
                continue
            if len(seen) >= MAX_FILES:
                self.problems.append(f"Stopped after {MAX_FILES} sitemap files.")
                break
            seen.add(url)
            kind, body, err = self.fetch_sitemap(url)
            if kind is None:
                self.problems.append(f"{url}: {err}")
                self.log(f"  ✗ {url}: {err}")
                continue
            if kind == "txt":
                n = self._add(((u, "") for u in body), url)
                self.files.append({"url": url, "parent": parent, "kind": "text", "count": n})
                self.log(f"  ✓ {url}: {n} URLs (text)")
                continue
            if local(body.tag) == "sitemapindex":
                kids = []
                for sm in body:
                    if local(sm.tag) != "sitemap":
                        continue
                    loc = next((c.text or "" for c in sm if local(c.tag) == "loc"), "").strip()
                    if loc:
                        kids.append(urljoin(url, loc))
                self.files.append({"url": url, "parent": parent, "kind": "index", "count": len(kids)})
                self.log(f"  ✓ {url}: index of {len(kids)} sitemaps")
                queue[0:0] = [(k, url) for k in kids]   # depth-first keeps sections together
                continue
            rows = []
            for u in body:
                if local(u.tag) != "url":
                    continue
                loc = lastmod = ""
                for c in u:
                    t = local(c.tag)
                    if t == "loc":
                        loc = (c.text or "").strip()
                    elif t == "lastmod":
                        lastmod = (c.text or "").strip()
                if loc:
                    rows.append((urljoin(url, loc), lastmod))
            n = self._add(rows, url)
            self.files.append({"url": url, "parent": parent, "kind": "urls", "count": n})
            self.log(f"  ✓ {url}: {n} URLs")

    def _add(self, rows, sitemap):
        n = 0
        for loc, lastmod in rows:
            n += 1
            if loc not in self.urls:
                self.urls[loc] = {"lastmod": lastmod, "sitemap": sitemap}
        return n

    # ---------- entry point ----------
    def run(self, address):
        address = with_scheme(address)
        if looks_like_sitemap(address):
            self.log(f"Reading sitemap {address}")
            self.read_all([address])
            if not self.urls:
                p = urlparse(address)
                self.log("That address gave no URLs — searching the site for its sitemap instead")
                self.problems.append("The address you gave didn't work, so the site was searched instead.")
                starts = [s for s in self.discover(f"{p.scheme}://{p.netloc}") if s != address]
                self.read_all(starts)
        else:
            self.log(f"Looking for sitemaps on {address}")
            starts = self.discover(address)
            for s in starts:
                self.log(f"  found {s}")
            self.read_all(starts)
        return self
