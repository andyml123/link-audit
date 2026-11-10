"""Audit a WordPress site's published posts for AI-writing tells.

python -m tellstool.run littleflowercottage.com [--skip-blockquotes] [--out tells]

Reads posts from the public WordPress REST API, checks the article prose
(paragraphs, list items, headings) and writes report.json and posts.csv.
Output goes outside docs/ so it is NOT published on GitHub Pages.
"""
import argparse
import collections
import csv
import datetime as dt
import html
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

from .checks import CHECKS, SCORED, check_block, word_count

USER_AGENT = "PersonalLinkAudit/1.0 (+private content audit of my own site)"

# Containers that hold plugin output, forms, tables of contents etc., not prose
SKIP_CLASS = re.compile(
    r"(optin|kit|form|amazon|strip|toc|ez-toc|related|readnext|crp|yarpp|quiz|checker|doctor|"
    r"compat|lfc-|wp-block-button|shortcode|share|social|author|comment|mv-|adthrive|raptive|"
    r"recipe|card|caption|wp-caption|gallery|table)",
    re.I,
)
SKIP_TAGS = {"form", "nav", "aside", "table", "figure", "figcaption", "script", "style", "noscript", "iframe", "button", "select"}


def fetch_posts(site):
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    posts, page = [], 1
    while True:
        url = f"{site}/wp-json/wp/v2/posts"
        params = {"per_page": 100, "page": page, "_fields": "id,link,title,content,date,modified"}
        for attempt in range(4):
            try:
                r = s.get(url, params=params, timeout=60)
            except requests.RequestException as e:
                r = None
                err = str(e)
            if r is not None and r.status_code in (200, 400):
                break
            time.sleep(5 * (attempt + 1))
        if r is None:
            sys.exit(f"Could not reach {url}: {err}")
        if r.status_code == 400 and page > 1:
            break  # past the last page
        if r.status_code != 200:
            sys.exit(f"{url} returned HTTP {r.status_code}. A firewall may be blocking the REST API.")
        batch = r.json()
        if not batch:
            break
        posts.extend(batch)
        total_pages = int(r.headers.get("X-WP-TotalPages", page))
        print(f"page {page}/{total_pages}: {len(posts)} posts", flush=True)
        if page >= total_pages:
            break
        page += 1
        time.sleep(0.5)
    return posts


def skipped(el, skip_blockquotes):
    for parent in el.parents:
        if parent.name in SKIP_TAGS:
            return True
        if skip_blockquotes and parent.name == "blockquote":
            return True
        cls = " ".join(parent.get("class") or []) + " " + (parent.get("id") or "")
        if cls.strip() and SKIP_CLASS.search(cls):
            return True
    return False


def blocks_from_html(raw, skip_blockquotes):
    soup = BeautifulSoup(raw, "lxml")
    for t in soup.find_all(list(SKIP_TAGS)):
        t.decompose()
    blocks, bold_lists = [], []
    for el in soup.find_all(["p", "li", "h2", "h3", "h4"]):
        if skipped(el, skip_blockquotes):
            continue
        if el.name == "li" and el.find(["ul", "ol"]):
            # Parent of a nested list: only its own text
            text = " ".join(t for t in el.find_all(string=True, recursive=False)).strip()
        else:
            text = el.get_text(" ", strip=True)
        text = re.sub(r"\[[^\]]{1,80}\]", " ", html.unescape(text))  # leftover shortcodes
        text = re.sub(r"\s+", " ", text).strip()
        if word_count(text) < 2:
            continue
        kind = "h" if el.name.startswith("h") else el.name
        blocks.append((kind, text))
    # Bold-label lists: 3+ items in one list opening with bold text then : or .
    for lst in soup.find_all(["ul", "ol"]):
        if skipped(lst, skip_blockquotes):
            continue
        items = lst.find_all("li", recursive=False)
        labelled = []
        for li in items:
            first = next((c for c in li.children if getattr(c, "name", None) or str(c).strip()), None)
            if getattr(first, "name", None) in ("strong", "b"):
                label = first.get_text(" ", strip=True)
                after = first.next_sibling
                after_txt = str(after.get_text() if hasattr(after, "get_text") else (after or ""))
                if label.endswith((":", ".")) or after_txt.lstrip().startswith((":", ".", "–", "—", "-")):
                    labelled.append(li.get_text(" ", strip=True))
        if len(labelled) >= 3:
            bold_lists.append(labelled)
    return blocks, bold_lists


def audit_post(p, skip_blockquotes):
    blocks, bold_lists = blocks_from_html(p["content"]["rendered"], skip_blockquotes)
    words = sum(word_count(t) for _, t in blocks)
    findings = []
    for kind, text in blocks:
        findings.extend(check_block(text, kind))
    for items in bold_lists:
        findings.append(("bold_label", items[0][:200], f"{len(items)} bold-labelled items"))
    counts = collections.Counter(f[0] for f in findings)
    sales_words = collections.Counter(f[2] for f in findings if f[0] == "sales")
    tells = sum(counts[k] for k in SCORED)
    title = html.unescape(BeautifulSoup(p["title"]["rendered"], "lxml").get_text())
    return {
        "id": p["id"],
        "url": p["link"],
        "title": title,
        "published": p.get("date"),
        "modified": p.get("modified"),
        "words": words,
        "tells": tells,
        "per_1k": round(tells * 1000 / words, 1) if words else 0,
        "triads": counts["triad"],
        "counts": {k: counts[k] for k in CHECKS},
        "sales_words": dict(sales_words),
        "findings": [{"check": c, "sentence": s, "match": m} for c, s, m in findings],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("site")
    ap.add_argument("--skip-blockquotes", action="store_true", help="ignore sample messages in blockquotes")
    ap.add_argument("--out", default="tells")
    args = ap.parse_args(argv)

    site = args.site.strip().rstrip("/")
    if not site.startswith("http"):
        site = "https://www." + site if site.count(".") == 1 else "https://" + site
    name = re.sub(r"^https?://(www\.)?", "", site)

    posts = fetch_posts(site)
    if not posts:
        sys.exit("No posts returned.")
    results = [audit_post(p, args.skip_blockquotes) for p in posts]
    results.sort(key=lambda r: (-r["per_1k"], -r["tells"]))

    totals = collections.Counter()
    sales_totals = collections.Counter()
    for r in results:
        totals.update(r["counts"])
        sales_totals.update(r["sales_words"])
    words = sum(r["words"] for r in results)
    tells = sum(r["tells"] for r in results)

    now = dt.datetime.now(dt.timezone.utc)
    out_dir = os.path.join(args.out, name, now.strftime("%Y-%m-%d-%H%M"))
    os.makedirs(out_dir, exist_ok=True)
    summary = {
        "site": name,
        "generated": now.isoformat(),
        "posts": len(results),
        "words": words,
        "tells": tells,
        "per_1k": round(tells * 1000 / words, 1) if words else 0,
        "triads": totals["triad"],
        "posts_with_tells": sum(1 for r in results if r["tells"]),
        "check_labels": CHECKS,
        "totals": {k: totals[k] for k in CHECKS},
        "sales_words": dict(sales_totals.most_common()),
        "skip_blockquotes": args.skip_blockquotes,
    }
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump({"summary": summary, "posts": results}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "posts.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "title", "words", "tells", "per_1k", "triads_review"] + list(CHECKS))
        for r in results:
            w.writerow([r["url"], r["title"], r["words"], r["tells"], r["per_1k"], r["triads"]] + [r["counts"][k] for k in CHECKS])
    print(json.dumps(summary, indent=1))
    print("Written to", out_dir)


if __name__ == "__main__":
    main()
