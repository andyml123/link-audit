"""Analysis: page types, search intent, TF-IDF similarity, link placement and anchors."""
import re
from collections import defaultdict
from urllib.parse import urlparse

import numpy as np
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

STOP = set(ENGLISH_STOP_WORDS) | {
    "also", "just", "really", "make", "makes", "way", "ways", "like", "use", "used", "using",
    "good", "great", "best", "new", "need", "want", "know", "thing", "things", "lot", "lots",
    "little", "bit", "time", "help", "tips", "ideas", "idea", "post", "article", "read",
    "click", "here", "guide", "easy", "simple", "one", "two", "three", "year", "years",
    "don", "doesn", "isn", "won", "ll", "ve", "re", "s", "t", "d", "m",
}
WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")

GENERIC_ANCHORS = {
    "click here", "here", "read more", "more", "learn more", "this", "this post", "this article",
    "this page", "this guide", "this link", "link", "continue reading", "see more", "find out more",
    "check it out", "check this out", "read this", "full post", "see here", "go here", "tap here",
    "click", "more info", "more information", "details", "see this", "view more", "this one",
    "read the full post", "read full article", "keep reading", "click this link", "visit",
    "website", "this website", "this site", "source", "via", "post", "article",
}

TYPE_LABELS = [
    "pillar_guide", "support_article", "product_or_service", "comparison_or_review",
    "glossary_or_definition", "tool_or_calculator", "news_or_update", "category_hub",
    "home", "about_or_company", "legal_or_utility", "other",
]
NO_SOURCE = {"home", "legal_or_utility", "category_hub"}
NO_TARGET = NO_SOURCE | {"about_or_company"}


def stem(w):
    w = w.lower().replace("’", "'")
    if w.endswith("'s"):
        w = w[:-2]
    w = w.strip("'-")
    if len(w) > 4:
        if w.endswith("ies"):
            return w[:-3] + "y"
        if w.endswith("es") and w[-3] in "sxz":
            return w[:-2]
        if w.endswith("s") and not w.endswith(("ss", "us", "is")):
            return w[:-1]
    return w


def tokenize(text):
    out = []
    for m in WORD.finditer(text):
        w = stem(m.group(0))
        if len(w) > 1 and w not in STOP and not w.isdigit():
            out.append(w)
    return out


# ---------------- classification ----------------
LEGAL = re.compile(r"(^|/)(privacy|terms|disclaimer|cookie|contact|disclosure|accessibility|"
                   r"sitemap|search|login|log-in|register|cart|checkout|my-account|account|"
                   r"policy|policies|dmca|write-for-us|advertise|affiliate-disclosure|"
                   r"subscribe|thank-you|thanks|unsubscribe|wp-login)(/|-|$)", re.I)
ABOUT = re.compile(r"(^|/)(about|about-us|about-me|our-story|meet-|team|who-we-are)", re.I)
HUB = re.compile(r"/(category|categories|tag|tags|author|topics?|archives?|page/\d+|series|"
                 r"collections?)(/|$)", re.I)
COMPARE = re.compile(r"\b(best|top \d+|\d+ best|vs\.?|versus|review|reviews|reviewed|compared|"
                     r"comparison|alternatives?|buying guide)\b", re.I)
DEFINE = re.compile(r"^(what (is|are|does|do)|meaning of|definition of|glossary)|\b(meaning|"
                    r"definition|defined)\b", re.I)
TOOL = re.compile(r"\b(calculator|generator|tool|quiz|planner|converter|finder|checker)\b", re.I)
NEWS = re.compile(r"(/news/|/updates?/|/changelog|/press/|\bannounc|\bupdate:)", re.I)
PILLAR = re.compile(r"\b(ultimate|complete|definitive|comprehensive|beginner'?s?|"
                    r"everything you need)\b.*\bguide\b|\bguide to\b|\b101\b", re.I)
TRANSACT = re.compile(r"\b(buy|price|prices|pricing|deal|deals|coupon|discount|order|shop|"
                      r"for sale|cheap)\b", re.I)


def classify(page, ctx_out):
    path = urlparse(page["url"]).path or "/"
    title = page.get("h1") or page.get("title") or ""
    wc = page.get("word_count", 0)
    types = {t.lower() for t in page.get("schema_types", [])}
    if path.strip("/") == "":
        ptype = "home"
    elif LEGAL.search(path):
        ptype = "legal_or_utility"
    elif ABOUT.search(path) or "aboutpage" in types:
        ptype = "about_or_company"
    elif HUB.search(path) or "collectionpage" in types and "article" not in " ".join(types) \
            or (wc < 350 and ctx_out >= 12):
        ptype = "category_hub"
    elif "newsarticle" in types or NEWS.search(path + " " + title):
        ptype = "news_or_update"
    elif page.get("has_tool") and TOOL.search(title + " " + path):
        ptype = "tool_or_calculator"
    elif ("product" in types and "review" not in types) or re.search(r"/(product|shop|services?)/", path):
        ptype = "product_or_service"
    elif COMPARE.search(title):
        ptype = "comparison_or_review"
    elif (DEFINE.search(title) and wc < 1300) or "/glossary/" in path or "definedterm" in types:
        ptype = "glossary_or_definition"
    elif wc >= 2500 or (wc >= 1800 and ctx_out >= 12) or (PILLAR.search(title) and wc >= 1500):
        ptype = "pillar_guide"
    elif wc >= 150:
        ptype = "support_article"
    else:
        ptype = "other"

    if ptype in ("home", "about_or_company", "category_hub", "legal_or_utility"):
        intent = "navigational"
    elif ptype == "product_or_service" or TRANSACT.search(title):
        intent = "transactional"
    elif ptype == "comparison_or_review":
        intent = "commercial"
    else:
        intent = "informational"
    return ptype, intent


# ---------------- anchors ----------------
def anchor_options(sentence, existing, weights, title_terms, title_bigrams=frozenset(),
                   common_word=lambda w: False, src_terms=frozenset(), k=3):
    """Find phrases in the sentence that match the target's defining terms."""
    toks = [(m.start(), m.end(), m.group(0)) for m in WORD.finditer(sentence)]
    stems = [stem(t[2]) for t in toks]
    existing_low = [e.lower() for e in existing]
    # A phrase may not run across punctuation.
    breaks = [bool(re.search(r"[,;:()\[\]–—\"“”!?.…/|]", sentence[toks[x][1]:toks[x + 1][0]]))
              for x in range(len(toks) - 1)]

    # title_terms = the target's focus words (title, H1, URL slug, headings, meta
    # description). weights = its strongest body terms, strongest first.
    strong = set(list(weights)[:12])

    def match_word(s):
        return s in title_terms or s in strong

    # A word also counts if, with a neighbour, it forms a two-word term from the
    # target's title ("snake plant") or pairs with a word that is itself distinctive.
    content_pos = [x for x in range(len(toks)) if stems[x] not in STOP and len(stems[x]) > 1]
    base = {x: match_word(stems[x]) for x in content_pos}
    matched = {}
    for ci, x in enumerate(content_pos):
        ok = base[x]
        for y, bg in ((content_pos[ci - 1], f"{stems[content_pos[ci-1]]} {stems[x]}") if ci > 0 else (None, None),
                      (content_pos[ci + 1], f"{stems[x]} {stems[content_pos[ci+1]]}")
                      if ci + 1 < len(content_pos) else (None, None)):
            if ok or y is None:
                continue
            if bg in title_bigrams or (base[y] and weights.get(bg, 0) > 0 and
                                       not common_word(stems[x])):
                ok = True
        matched[x] = ok

    def match_at(x):
        return matched.get(x, False)

    cands = []
    n = len(toks)
    for i in range(n):
        if stems[i] in STOP or stems[i].isdigit() or not match_at(i):
            continue
        for j in range(i, min(n, i + 5)):
            if j > i and breaks[j - 1]:
                break
            if stems[j] in STOP or stems[j].isdigit() or not match_at(j):
                continue
            content = [s for s in stems[i:j + 1] if s not in STOP and len(s) > 1]
            if not any(c in title_terms for c in content):
                continue  # must name something the target page is actually about
            if any(x in matched and not matched[x] for x in range(i, j + 1)):
                continue
            if len(set(content)) < len(content):
                continue  # "low and low"
            phrase_low = sentence[toks[i][0]:toks[j][1]].lower()
            if re.search(r"\b(and|or|but|nor)\b", phrase_low) and \
                    " ".join(content) not in " ".join(title_terms):
                continue  # two separate things joined up is not an anchor
            score = sum(weights.get(s, 0.0) for s in content)
            score += 1.5 * sum(weights.get(f"{a} {b}", 0.0) for a, b in zip(content, content[1:]))
            score += 0.04 * sum(1 for s in content if s in title_terms)
            # Using the source page's own main keyword as an anchor to another page
            # invites cannibalisation, so prefer phrases with target-specific words.
            if src_terms and all(s in src_terms for s in content):
                continue
            length = j - i + 1
            score *= {1: 0.55, 2: 1.0, 3: 1.1, 4: 1.0, 5: 0.85}[length]
            phrase = sentence[toks[i][0]:toks[j][1]]
            low = phrase.lower()
            if any(low in e or e in low for e in existing_low):
                continue
            cands.append((score, toks[i][0], toks[j][1], phrase))
    cands.sort(key=lambda c: -c[0])
    picked = []
    for c in cands:
        if any(not (c[2] <= p[1] or c[1] >= p[2]) for p in picked) and len(picked) >= 1:
            # overlapping alternative: keep only if it's a genuinely different length
            if any(c[3].lower() in p[3].lower() or p[3].lower() in c[3].lower() for p in picked):
                continue
        picked.append(c)
        if len(picked) >= k:
            break
    return [(p[3], p[0]) for p in picked]


def link_role(src, tgt, anchor, src_text_low):
    tt = tgt["type"]
    if tt == "pillar_guide" and src["type"] != "pillar_guide":
        return "broader_guide"
    if tt == "comparison_or_review":
        return "comparison_or_alternative"
    if tt == "product_or_service":
        return "product_or_service"
    if tt == "tool_or_calculator":
        return "next_step"
    if tt == "glossary_or_definition":
        return "explains_concept"
    mentions = src_text_low.count(anchor.lower())
    if tgt["word_count"] < 900 and len(anchor.split()) <= 3 and mentions <= 2:
        return "explains_concept"
    if src["type"] == "pillar_guide" or mentions <= 2:
        return "deeper_detail"
    return "next_step"


ROLE_WHY = {
    "explains_concept": "explains “{a}”, which this page mentions without explaining",
    "deeper_detail": "goes deeper into “{a}”, which this page only touches on",
    "broader_guide": "is the broader guide this page sits under",
    "next_step": "is a natural next read after this section",
    "product_or_service": "is the product/service that solves what this section discusses",
    "comparison_or_alternative": "compares options for “{a}”",
}


# ---------------- main ----------------
def analyse(crawler, log=print, per_source=8, min_sim=0.06):
    pages = list(crawler.pages.values())
    aliases = crawler.aliases
    keys = {p["key"] for p in pages}

    def resolve(k):
        seen = 0
        while k in aliases and seen < 5:
            k = aliases[k]
            seen += 1
        return k

    # ---- link graph ----
    ctx_in, total_in, ctx_out = defaultdict(set), defaultdict(set), defaultdict(set)
    existing_pairs = set()
    for p in pages:
        for l in p["links"]:
            tk = resolve(l["to_key"])
            l["to_key"] = tk
            if tk == p["key"] or tk not in keys:
                continue
            total_in[tk].add(p["key"])
            existing_pairs.add((p["key"], tk))
            if l["ctx"]:
                ctx_in[tk].add(p["key"])
                ctx_out[p["key"]].add(tk)

    for p in pages:
        p["type"], p["intent"] = classify(p, len(ctx_out[p["key"]]))
        p["ctx_in"] = len(ctx_in[p["key"]])
        p["total_in"] = len(total_in[p["key"]])
        p["ctx_out"] = len(ctx_out[p["key"]])

    usable = [p for p in pages if not p.get("noindex")]
    n = len(usable)
    log(f"Analysing {n} indexable pages")
    result_pages = pages
    suggestions = []
    if n >= 2:
        def doc_text(p):
            heads = " ".join(s["t"] for s in p["sentences"] if s["h"])
            body = " ".join(s["t"] for s in p["sentences"] if not s["h"])
            t = p.get("title", "")
            return f"{t} {t} {t} {p.get('h1','')} {p.get('h1','')} {heads} {heads} {body}"

        vec = TfidfVectorizer(
            tokenizer=tokenize, lowercase=False, token_pattern=None, ngram_range=(1, 2),
            sublinear_tf=True, max_df=0.6 if n >= 30 else 1.0, min_df=2 if n >= 60 else 1,
            max_features=200_000,
        )
        X = vec.fit_transform([doc_text(p) for p in usable])
        vocab = vec.get_feature_names_out()
        vocab_idx = vec.vocabulary_
        idx_of = {p["key"]: i for i, p in enumerate(usable)}

        # Sentence matrix for placements, computed once.
        sent_rows, sent_texts = [], []
        for p in usable:
            start = len(sent_texts)
            heading = ""
            for s in p["sentences"]:
                if s["h"]:
                    heading = s["t"]
                    continue
                s["sec"] = heading
                sent_texts.append(s["t"])
            sent_rows.append((start, len(sent_texts)))
        S = vec.transform(sent_texts) if sent_texts else None

        # Target term weights (top terms) for anchor finding. Words that appear on a big
        # share of the site (brand words, "water", "plant") never define a single page.
        df_ratio = np.asarray((X > 0).sum(axis=0)).ravel() / n
        common = df_ratio > (0.6 if n >= 10 else 0.95)

        def is_common(w):
            c = vocab_idx.get(w)
            return c is not None and bool(common[c])

        def top_terms(i, k=40):
            row = X[i]
            if row.nnz == 0:
                return {}
            order = np.argsort(-row.data)
            out = {}
            for o in order:
                col = row.indices[o]
                if common[col]:
                    continue
                out[vocab[col]] = float(row.data[o])
                if len(out) >= k:
                    break
            return out

        term_cache = {}
        src_ok = [p["type"] not in NO_SOURCE and p["word_count"] >= 150 for p in usable]
        tgt_ok = np.array([p["type"] not in NO_TARGET for p in usable])
        boost = np.array([1.6 if p["ctx_in"] == 0 else 1.35 if p["ctx_in"] < 3 else 1.0
                          for p in usable])

        chunk = 400
        for c0 in range(0, n, chunk):
            sims = (X[c0:c0 + chunk] @ X.T).toarray()
            for r in range(sims.shape[0]):
                i = c0 + r
                if not src_ok[i]:
                    continue
                src = usable[i]
                row = sims[r] * boost
                row[i] = -1
                row[~tgt_ok] = -1
                for tk in ctx_out[src["key"]]:
                    if tk in idx_of:
                        row[idx_of[tk]] = -1
                cand = [j for j in np.argsort(-row)[:per_source] if sims[r][j] >= min_sim and row[j] > 0]
                if not cand:
                    continue
                s0, s1 = sent_rows[i]
                if S is None or s1 <= s0:
                    continue
                body_sents = [s for s in src["sentences"] if not s["h"]]
                src_low = " ".join(s["t"] for s in body_sents).lower()
                src_terms = frozenset(tokenize(src.get("h1") or src.get("title") or ""))
                used_sent = set()
                for j in cand:
                    tgt = usable[j]
                    if j not in term_cache:
                        tt = tokenize(tgt.get("h1") or tgt.get("title") or "")
                        slug = urlparse(tgt["url"]).path.rstrip("/").split("/")[-1].replace("-", " ")
                        heads = " ".join(x["t"] for x in tgt["sentences"] if x["h"])
                        focus = tt + tokenize(tgt.get("title", "")) + tokenize(slug) + \
                            tokenize(heads) + tokenize(tgt.get("description", ""))
                        title_terms = {t for t in focus if not is_common(t)}
                        title_bigrams = {f"{a} {b}" for a, b in zip(tt, tt[1:])}
                        term_cache[j] = (top_terms(j), title_terms, title_bigrams)
                    weights, title_terms, title_bigrams = term_cache[j]
                    ssims = (S[s0:s1] @ X[j].T).toarray().ravel()
                    best = None
                    for si in np.argsort(-ssims)[:4]:
                        if ssims[si] <= 0 or si in used_sent:
                            continue
                        sent = body_sents[si]
                        if len(sent["t"].split()) > 70:
                            continue
                        opts = anchor_options(sent["t"], sent["a"], weights, title_terms, title_bigrams,
                                              is_common, src_terms)
                        if not opts:
                            continue
                        sc = float(ssims[si]) + min(0.5, opts[0][1])
                        if best is None or sc > best[0]:
                            best = (sc, si, sent, opts, float(ssims[si]))
                    if best is None:
                        continue
                    _, si, sent, opts, ssim = best
                    used_sent.add(si)
                    anchor = opts[0][0]
                    role = link_role(src, tgt, anchor, src_low)
                    doc_sim = float(sims[r][j])
                    conf = 0.5 * min(1.0, doc_sim / 0.45) + 0.3 * min(1.0, ssim / 0.35) + \
                        0.2 * min(1.0, opts[0][1] / 0.35)
                    if tgt["ctx_in"] == 0:
                        conf = min(1.0, conf + 0.05)
                    tname = tgt.get("h1") or tgt.get("title") or tgt["url"]
                    suggestions.append({
                        "source": src["url"], "source_title": src.get("h1") or src.get("title"),
                        "target": tgt["url"], "target_title": tname,
                        "section": sent.get("sec", ""), "sentence": sent["t"], "anchor": anchor,
                        "alt_anchors": [o[0] for o in opts[1:]], "role": role,
                        "confidence": round(conf * 100), "similarity": round(doc_sim, 3),
                        "target_ctx_in": tgt["ctx_in"],
                        "why": "Target " + ROLE_WHY[role].format(a=anchor) + ".",
                        "nav_link_exists": (src["key"], tgt["key"]) in existing_pairs,
                    })
        log(f"Generated {len(suggestions)} link suggestions")

        # If an anchor is exactly another page's core topic ("snake plants" →
        # /snake-plant-care/), point it there instead – that's the page a reader expects.
        GENERIC_SLUG = {"care", "guide", "how", "tip", "best", "complete", "ultimate", "easy",
                        "way", "grow", "growing", "indoor", "ideas", "idea", "list", "beginner"}
        core_to_page = {}
        for p in usable:
            if p["type"] in NO_TARGET:
                continue
            slug = urlparse(p["url"]).path.rstrip("/").split("/")[-1].replace("-", " ")
            core = tuple(t for t in tokenize(slug) if t not in GENERIC_SLUG)
            if core and core not in core_to_page:
                core_to_page[core] = p
        by_url = {p["url"]: p for p in usable}
        seen_pairs = set()
        rerouted = []
        suggestions.sort(key=lambda x: -x["confidence"])
        for s in suggestions:
            core = tuple(t for t in tokenize(s["anchor"]) if t not in GENERIC_SLUG)
            alt = core_to_page.get(core)
            src = by_url[s["source"]]
            if alt and alt["url"] != s["target"] and alt["url"] != s["source"] and \
                    alt["key"] not in ctx_out[src["key"]]:
                s["target"] = alt["url"]
                s["target_title"] = alt.get("h1") or alt.get("title") or alt["url"]
                s["target_ctx_in"] = alt["ctx_in"]
                s["role"] = link_role(src, alt, s["anchor"], "")
                s["why"] = "Anchor is this page’s exact topic — target " + \
                    ROLE_WHY[s["role"]].format(a=s["anchor"]) + "."
                s["nav_link_exists"] = (src["key"], alt["key"]) in existing_pairs
                s["confidence"] = min(100, s["confidence"] + 8)
            pair = (s["source"], s["target"])
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            rerouted.append(s)
        suggestions = rerouted

    suggestions.sort(key=lambda s: -s["confidence"])
    sugg_in, sugg_out = defaultdict(list), defaultdict(int)
    for s in suggestions:
        sugg_in[s["target"]].append(s)
        sugg_out[s["source"]] += 1

    table = []
    for p in result_pages:
        table.append({
            "url": p["url"], "title": p.get("h1") or p.get("title"), "type": p["type"],
            "intent": p["intent"], "word_count": p["word_count"], "ctx_in": p["ctx_in"],
            "total_in": p["total_in"], "ctx_out": p["ctx_out"],
            "suggested_in": len(sugg_in[p["url"]]), "suggested_out": sugg_out[p["url"]],
            "noindex": p.get("noindex", False), "published": p.get("published", ""),
        })
    table.sort(key=lambda r: (r["ctx_in"], -r["word_count"]))

    weak = []
    for r in table:
        if r["type"] in NO_TARGET or r["noindex"] or r["ctx_in"] > 2:
            continue
        srcs = sorted(sugg_in[r["url"]], key=lambda s: -s["confidence"])[:6]
        weak.append({**r, "status": "orphan" if r["total_in"] == 0 else
                     "no contextual links" if r["ctx_in"] == 0 else "weak",
                     "recommended_sources": [{"source": s["source"], "source_title": s["source_title"],
                                              "anchor": s["anchor"], "confidence": s["confidence"]}
                                             for s in srcs]})

    generic = []
    for p in result_pages:
        if p["type"] in NO_SOURCE:
            continue  # theme templates (archives, home) – not editorial copy
        for l in p["links"]:
            if not l["ctx"]:
                continue
            a = re.sub(r"[^\w\s]", "", l["anchor"].lower()).strip()
            a = re.sub(r"\s+", " ", a)
            bare = bool(re.match(r"^(https?://|www\.)", l["anchor"].strip().lower()))
            if a in GENERIC_ANCHORS or bare or a.startswith(("click here", "read more", "learn more")):
                generic.append({"source": p["url"], "source_title": p.get("h1") or p.get("title"),
                                "target": l["to"], "anchor": l["anchor"],
                                "problem": "bare URL" if bare else "generic wording"})

    type_counts = defaultdict(int)
    for r in table:
        type_counts[r["type"]] += 1
    stats = {
        "pages": len(table), "indexable": n,
        "orphans": sum(1 for w in weak if w["status"] == "orphan"),
        "no_contextual": sum(1 for w in weak if w["status"] == "no contextual links"),
        "weak": len(weak), "suggestions": len(suggestions), "generic_anchors": len(generic),
        "contextual_links": sum(r["ctx_out"] for r in table),
        "errors": len(crawler.errors), "blocked_by_robots": len(crawler.skipped_robots),
        "types": dict(type_counts), "discovery": crawler.discovery,
        "sitemaps": crawler.sitemaps_used[:50], "crawl_delay": crawler.crawl_delay,
    }
    return {"stats": stats, "pages": table, "suggestions": suggestions, "weak": weak,
            "generic": generic, "errors": crawler.errors[:2000]}
