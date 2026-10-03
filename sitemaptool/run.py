"""Run: python -m sitemaptool.run <site-or-sitemap> --out docs"""
import argparse
import csv
import html
import json
import os
import shutil
import sys
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

from linktool.report import CSS
from .extract import Extractor, with_scheme

EXTRA_CSS = r"""
.list{list-style:none;margin:0;padding:0}
.list li{display:flex;gap:10px;align-items:baseline;padding:7px 0;border-bottom:1px solid var(--line);min-width:0}
.list li a{flex:1;min-width:0;word-break:break-all;font-size:.92rem;text-decoration:none}
.list li .d{flex:none;font-size:.75rem;color:var(--muted);font-variant-numeric:tabular-nums}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0}
.bar .count{color:var(--muted);font-size:.85rem;margin-left:auto}
table.t{width:100%;border-collapse:collapse;font-size:.85rem}
table.t td{padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
table.t td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
table.t td.u{word-break:break-all}
.scroll{overflow-x:auto}
.stat b{font-size:1.15rem;word-break:break-word}
.ind{color:var(--muted)}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const $ = s => document.querySelector(s);
const q = $('#q'), sec = $('#sec'), list = $('#list'), cnt = $('#count'), more = $('#more');
let shown = 300, rows = D.urls;
function section(u){try{const p=new URL(u).pathname.split('/').filter(Boolean);return p.length>1?'/'+p[0]+'/':'/ (top level)'}catch(e){return ''}}
function filter(){
  const t = q.value.trim().toLowerCase(), s = sec.value;
  rows = D.urls.filter(r => (!t || r[0].toLowerCase().includes(t)) && (!s || section(r[0]) === s));
  shown = 300; draw();
}
function draw(){
  list.textContent = '';
  const frag = document.createDocumentFragment();
  for (const r of rows.slice(0, shown)) {
    const li = document.createElement('li'), a = document.createElement('a');
    a.href = r[0]; a.textContent = r[0]; a.target = '_blank'; a.rel = 'noopener';
    li.append(a);
    if (r[1]) { const d = document.createElement('span'); d.className = 'd'; d.textContent = r[1].slice(0,10); li.append(d); }
    frag.append(li);
  }
  list.append(frag);
  cnt.textContent = rows.length === D.urls.length ? `${rows.length.toLocaleString()} URLs` : `${rows.length.toLocaleString()} of ${D.urls.length.toLocaleString()}`;
  more.hidden = rows.length <= shown;
  more.textContent = `Show ${Math.min(1000, rows.length - shown).toLocaleString()} more`;
}
function toast(m){const t=$('#toast');t.textContent=m;t.classList.add('on');setTimeout(()=>t.classList.remove('on'),1600)}
async function copy(text, label){
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch(e){ const ta=document.createElement('textarea'); ta.value=text; document.body.append(ta); ta.select();
    try{document.execCommand('copy'); toast(label)}catch(_){toast('Copy failed — use the .txt download')} ta.remove(); }
}
$('#copy').onclick = () => copy(rows.map(r => r[0]).join('\n'), `Copied ${rows.length.toLocaleString()} URLs`);
more.onclick = () => { shown += 1000; draw(); };
q.oninput = filter; sec.onchange = filter;
const secs = {}; D.urls.forEach(r => { const s = section(r[0]); secs[s] = (secs[s]||0)+1; });
Object.entries(secs).sort((a,b)=>b[1]-a[1]).forEach(([s,n]) => { const o=document.createElement('option'); o.value=s; o.textContent=`${s} (${n})`; sec.append(o); });
draw();
"""


def esc(s):
    return html.escape(str(s), quote=True)


def write_result(ex, address, out_root, stamp):
    host = urlparse(with_scheme(address)).netloc.lower().removeprefix("www.") or "unknown"
    site_dir = os.path.join(out_root, "sitemaps", host)
    run_dir = os.path.join(site_dir, stamp)
    os.makedirs(run_dir, exist_ok=True)

    urls = [[u, v["lastmod"], v["sitemap"]] for u, v in ex.urls.items()]
    with open(os.path.join(run_dir, "urls.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(u[0] for u in urls) + ("\n" if urls else ""))
    with open(os.path.join(run_dir, "urls.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "lastmod", "sitemap"])
        w.writerows(urls)
    summary = {"address": address, "generated": stamp, "urls": len(urls),
               "files": len(ex.files), "problems": ex.problems}
    with open(os.path.join(run_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)

    dates = sorted(u[1][:10] for u in urls if u[1])
    dupes = sum(f["count"] for f in ex.files if f["kind"] != "index") - len(urls)
    stats = [(f"{len(urls):,}", "URLs"),
             (f"{sum(1 for f in ex.files if f['kind'] != 'index')}", "sitemap files"),
             (dates[-1] if dates else "—", "newest lastmod"),
             (dates[0] if dates else "—", "oldest lastmod"),
             (f"{max(dupes, 0):,}", "duplicates removed"),
             (f"{len(ex.problems)}", "problems")]
    stat_html = "".join(f'<div class="stat"><b>{esc(v)}</b><span>{esc(k)}</span></div>' for v, k in stats)

    file_rows = []
    for f in ex.files:
        ind = '<span class="ind">↳ </span>' if f["parent"] else ""
        label = {"index": "index", "urls": "URLs", "text": "text"}[f["kind"]]
        file_rows.append(f'<tr><td class="u">{ind}<a href="{esc(f["url"])}" target="_blank" rel="noopener">'
                         f'{esc(f["url"])}</a></td><td>{label}</td><td class="n">{f["count"]:,}</td></tr>')
    tried = "".join(f'<li><span class="url">{esc(a)}</span> — {esc(r)}</li>' for a, r in ex.checked)
    probs = "".join(f"<li>{esc(p)}</li>" for p in ex.problems)

    if urls:
        body = f"""
<div class="bar"><input type="search" id="q" placeholder="Filter URLs (e.g. /houseplants/)" aria-label="Filter URLs"
 style="font:inherit;font-size:16px;padding:8px 10px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);flex:1 1 220px;min-width:0">
<select id="sec" aria-label="Section" style="font:inherit;font-size:16px;padding:8px 10px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);min-width:0;max-width:100%">
<option value="">All sections</option></select></div>
<div class="bar"><button class="b pri" id="copy" type="button">Copy URLs</button>
<span class="dl"><a href="urls.txt">urls.txt</a><a href="urls.csv">urls.csv</a></span><span class="count" id="count"></span></div>
<div class="card"><ul class="list" id="list"></ul><button class="more" id="more" type="button" hidden></button></div>"""
    else:
        body = '<div class="card empty">No URLs found. The checks below show what was tried.</div>'

    page = f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Sitemap · {esc(host)}</title><style>{CSS}{EXTRA_CSS}</style></head><body><div class="wrap">
<p class="sub"><a href="../../">All sitemap extracts</a></p>
<h1>{esc(host)} sitemap</h1><p class="sub">From {esc(address)} · extracted {esc(stamp)} UTC</p>
<div class="stats">{stat_html}</div>
{body}
{f'<div class="card"><div class="lbl">Problems</div><ul class="src">{probs}</ul></div>' if probs else ''}
{f'<details class="card"><summary>Sitemap files ({len(ex.files)})</summary><div class="scroll"><table class="t">{"".join(file_rows)}</table></div></details>' if file_rows else ''}
{f'<details class="card"{" open" if not urls else ""}><summary>How the sitemap was found</summary><ul class="src">{tried}</ul></details>' if tried else ''}
</div><div class="toast" id="toast" role="status"></div>
<script type="application/json" id="data">{json.dumps({"urls": [u[:2] for u in urls]}).replace("</", "<\\/")}</script>
{f"<script>{JS}</script>" if urls else ""}
</body></html>"""
    with open(os.path.join(run_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
    return site_dir, run_dir


def prune(site_dir, keep):
    runs = sorted([d for d in os.listdir(site_dir) if d[:4].isdigit()], reverse=True)
    for old in runs[keep:]:
        shutil.rmtree(os.path.join(site_dir, old), ignore_errors=True)


def write_index(out_root):
    root = os.path.join(out_root, "sitemaps")
    rows = []
    for host in sorted(os.listdir(root)):
        sdir = os.path.join(root, host)
        if not os.path.isdir(sdir):
            continue
        runs = sorted([d for d in os.listdir(sdir) if d[:4].isdigit()], reverse=True)
        if not runs:
            continue
        with open(os.path.join(sdir, "index.html"), "w", encoding="utf-8") as f:
            f.write(f'<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
                    f'<meta http-equiv="refresh" content="0;url={runs[0]}/"><a href="{runs[0]}/">Latest extract</a>')
        items = []
        for r in runs:
            try:
                with open(os.path.join(sdir, r, "summary.json"), encoding="utf-8") as f:
                    st = json.load(f)
            except Exception:
                st = {}
            addr = st.get("address", "")
            path = urlparse(with_scheme(addr)).path if addr else ""
            what = path if path not in ("", "/") else "Whole site"
            n = st.get("urls")
            items.append(f'<li><a href="{esc(host)}/{r}/">{esc(what)}</a>'
                         f'<span class="d">{f"{n:,} URLs" if isinstance(n, int) else ""} · {esc(r[:10])} {esc(r[11:13])}:{esc(r[13:15])} UTC</span></li>')
        rows.append(f'<div class="card"><div class="ttl">{esc(host)}</div>'
                    f'<ul class="list">{"".join(items)}</ul></div>')
    page = f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">
<title>Sitemap extracts</title><style>{CSS}{EXTRA_CSS}</style></head><body><div class="wrap">
<p class="sub"><a href="../">Internal link reports</a></p>
<h1>Sitemap extracts</h1><p class="sub">Run a new one from the repo's Actions tab → “Extract sitemap URLs” → Run workflow.</p>
{''.join(rows) or '<div class="empty">No extracts yet.</div>'}</div></body></html>"""
    with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("address", help="a site (example.com) or a sitemap URL")
    ap.add_argument("--out", default="docs")
    ap.add_argument("--keep", type=int, default=20)
    a = ap.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    ex = Extractor().run(a.address)
    site_dir, run_dir = write_result(ex, a.address.strip(), a.out, stamp)
    prune(site_dir, a.keep)
    write_index(a.out)
    print(f"{len(ex.urls)} URLs from {len(ex.files)} sitemap file(s) → {run_dir}")
    if ex.problems:
        print("Problems:\n  " + "\n  ".join(ex.problems))
    # Write the page even when nothing was found, so the result shows what was tried.
    return 0


if __name__ == "__main__":
    sys.exit(main())
