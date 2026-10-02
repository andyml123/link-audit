"""Command line entry: python -m linktool.run https://example.com"""
import argparse
import datetime as dt
import os
import shutil
import sys
from urllib.parse import urlparse

from .analyse import analyse
from .crawl import Crawler, host_key
from .report import write_home, write_report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Personal internal-link audit")
    ap.add_argument("site", help="Site URL, e.g. https://littleflowercottage.com")
    ap.add_argument("--max-pages", type=int, default=0, help="0 = no limit")
    ap.add_argument("--workers", type=int, default=4, help="parallel fetches (ignored if robots.txt sets Crawl-delay)")
    ap.add_argument("--delay", type=float, default=0.25, help="seconds between requests when no Crawl-delay")
    ap.add_argument("--out", default="docs", help="GitHub Pages folder")
    ap.add_argument("--keep", type=int, default=6, help="past reports to keep per site")
    args = ap.parse_args(argv)

    site = args.site.strip()
    if not site.startswith("http"):
        site = "https://" + site
    name = host_key(urlparse(site).netloc)
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%d-%H%M")

    crawler = Crawler(site, max_pages=args.max_pages, workers=args.workers, delay=args.delay).run()
    if not crawler.pages:
        print("No pages could be fetched. Check the URL, or whether a firewall blocks the crawler.")
        for e in crawler.errors[:10]:
            print("  ", e["url"], "→", e["error"])
        sys.exit(1)
    report = analyse(crawler)
    out = os.path.join(args.out, "reports", name, stamp)
    path = write_report(report, out, name, now.strftime("%d %b %Y, %H:%M UTC"))
    # Keep the newest few runs per site so the repo doesn't grow forever.
    site_dir = os.path.join(args.out, "reports", name)
    runs = sorted((d for d in os.listdir(site_dir) if d[:4].isdigit()), reverse=True)
    for old in runs[args.keep:]:
        shutil.rmtree(os.path.join(site_dir, old), ignore_errors=True)
    write_home(os.path.join(args.out, "reports"), args.out)
    print(f"Report written to {path}")
    st = report["stats"]
    print(f"{st['pages']} pages · {st['orphans']} orphans · {st['weak']} weak · "
          f"{st['suggestions']} suggestions · {st['generic_anchors']} generic anchors")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### {name}\n\n{st['pages']} pages · {st['orphans']} orphans · {st['weak']} weak · "
                    f"{st['suggestions']} suggestions\n\nReport folder: `reports/{name}/{stamp}/`\n")


if __name__ == "__main__":
    main()
