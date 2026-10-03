# Personal internal-link audit

A private copy of the "crawl my site and suggest internal links" idea, with no page limit and no credits. GitHub runs the crawl for free; the report appears as a web page on your GitHub Pages site.

## What it does

- Reads robots.txt (obeys Disallow and Crawl-delay), finds your sitemaps, and takes pages from every sitemap file in turn. No sitemap → follows links from the home page.
- Skips duplicates whose canonical URL points elsewhere and fetches the canonical page instead.
- Records every internal link, its anchor text, and whether it sits in the article body (contextual) or in menus, sidebars, related-post blocks and footers.
- Scores every page with TF-IDF, picks the 8 most similar pages for each article, boosts pages with fewer than 3 contextual links in, and finds the 4 sentences on the source page closest to the target.
- Picks anchor phrases that already exist word-for-word in those sentences and name what the target page is about (its title, URL slug, headings or meta description). Phrases never run across punctuation, never reuse the source page's own main keyword, and an anchor that is exactly another page's topic is routed to that page.
- Classifies each page (pillar guide, support article, comparison/review, definition, tool, news, hub, home, about, legal, other) and its intent (informational, commercial, transactional, navigational). Home, legal and hub pages are never sources or targets; about pages are never targets.
- Labels each link's job: explains concept, deeper detail, broader guide, next step, product/service, comparison.

The page types and roles come from rules rather than an AI model, so a run costs nothing. If a call looks wrong, the confidence score and the "why" line show what it was based on.

## Running a crawl (iPhone)

1. Open the repo on github.com in Safari → **Actions** → **Crawl a site** → **Run workflow**.
2. Pick a site (or choose "other" and type a URL), leave the page limit at 0 for no limit, and tap **Run workflow**.
3. When the run goes green (about 5–15 minutes for 1,000–2,500 pages), open your Pages site: `https://getwellmessages.net/link-audit/` (andyml123.github.io redirects there, because getwellmessages.net is your GitHub Pages domain). Each site's newest report is always at `/reports/<domain>/` — bookmark that.

GitHub emails you if a run fails. To get an email on success too: github.com → Settings → Notifications → Actions → untick "Only notify for failed workflows".

## Using the report

- **Weak pages** — orphans (no internal links at all), nav-only pages (linked only from menus/related posts), and pages with 1–2 contextual links, each with the pages that should link to it.
- **Suggestions** — sentence with the anchor highlighted, target, role and confidence. Copy link / Copy anchor buttons, and **Mark done** (remembered on that phone). Tick **By page** to group by the page you'll edit; **Copy for Claude** copies all of that page's links as instructions you can paste into a chat.
- **All pages** — type, intent, words, links in (contextual / total), links out, suggestions in and out.
- **Generic anchors** — "click here", "read more", bare URLs in article text.
- CSV and JSON downloads at the bottom.

## Extracting a sitemap (iPhone)

1. Repo on github.com → **Actions** → **Extract sitemap URLs** → **Run workflow**.
2. Type either a website (`example.com`) or a sitemap address (`https://example.com/sitemap_index.xml`) and tap **Run workflow**.
3. When the run goes green (usually under a minute), open `https://getwellmessages.net/link-audit/sitemaps/`. Each site's latest extract is at `/sitemaps/<domain>/`.

For a website it checks robots.txt for Sitemap lines, then the usual locations (sitemap_index.xml, sitemap.xml, wp-sitemap.xml and others), then the home page. Sitemap indexes are followed all the way down; .gz and plain-text sitemaps work. If a sitemap address you give doesn't work, it searches the site instead.

The result page lists every URL with its last-modified date, a filter box, a section picker (by first folder), a **Copy URLs** button (copies whatever the filter shows), and urls.txt / urls.csv downloads. "How the sitemap was found" shows each address checked. Every extract is listed separately under its site, by the sitemap address you gave ("Whole site" if you gave just the domain). The last 20 per site are kept.

## Settings

Edit the options list in `.github/workflows/crawl.yml` to change the sites in the picker. Command line options in `linktool/run.py`: `--workers` (parallel fetches, default 4), `--delay` (seconds between requests, default 0.25; Crawl-delay overrides both), `--keep` (past reports kept per site, default 6).

If a site's firewall (Cloudflare, Wordfence, your ad management's bot rules) blocks the crawler, the run fails with HTTP 403 errors. Allow the user agent `PersonalLinkAudit` or GitHub's IP ranges.

## Privacy

GitHub Pages from a free account needs a public repo, so the reports are publicly reachable if someone has the URL. They're marked noindex and the Pages robots.txt blocks crawlers, and everything in them is already public on your sites — but it does show your link gaps to anyone who finds it.

## One-time setup

1. Create a repo on github.com (e.g. `link-audit`, public).
2. Add these files (or let Claude push them).
3. Settings → Pages → Source: "Deploy from a branch" → Branch `main`, folder `/docs` → Save.
