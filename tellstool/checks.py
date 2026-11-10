"""Pattern checks for AI-writing tells in post prose.

Each check takes a block (paragraph, list item or heading) and returns
findings: (check_id, sentence, matched_text). Checks are deliberately
conservative: a pattern a script can't judge reliably (forced triads)
is reported separately as "review" and never counted in the score.
"""
import re

SENT_SPLIT = re.compile(r'(?<=[.!?])["”’)]*\s+(?=[A-Z0-9"“‘(])')

# Labels shown in the report, in display order
CHECKS = {
    "not_x_but_y": "Not X but Y",
    "closer": "Closers and fragments",
    "ing_tail": "Comment tails (-ing)",
    "inflated": "Inflated significance",
    "sales": "Sales words",
    "no_one": "Arguing with no one",
    "bold_label": "Bold-label lists (review)",
    "soft_sales": "\"Perfect\" (review)",
    "triad": "Possible forced triad (review)",
}
# Review-only checks are listed but not counted: spec lists, "perfect" and
# triads are often fine, so they need a human eye rather than a score.
REVIEW = {"bold_label", "soft_sales", "triad"}
SCORED = [k for k in CHECKS if k not in REVIEW]

# 1. Not X but Y
NOT_X_PATTERNS = [
    # "isn't X, it's Y" / "isn't X. It's Y" within one or two sentences
    re.compile(r"\b(?:is|are|was|were|it's|that's|this is|it is)\s*n(?:o|')t\s+(?:just|only|simply|merely|about)?\b[^.!?]{0,60}?[,.;:—–-]\s*(?:it's|it is|they're|they are|that's|this is|but)\b", re.I),
    re.compile(r"\b(?:is|are|was|were)n't\s+(?:just|only|simply|merely)\b", re.I),
    re.compile(r"\bnot\s+(?:just|only|simply|merely)\s+(?:a|an|the|about)?\s*\w+[^.!?]{0,50}?,?\s+(?:but|it's|it is)\b", re.I),
    re.compile(r"\bmore than just\b", re.I),
    # sentence ending on ", not X."
    re.compile(r",\s+not\s+(?:a\s+|an\s+|the\s+|just\s+)?[\w'’ -]{1,30}[.!]$", re.I),
]

# 4. Comment tails: ", allowing ..." at the end of a sentence
ING_WORDS = (
    "allowing|ensuring|making|giving|helping|creating|providing|highlighting|"
    "enabling|adding|offering|resulting|leaving|turning|letting|bringing|"
    "transforming|showcasing|reflecting|underscoring|emphasizing|emphasising|"
    "fostering|promoting|contributing|demonstrating|signaling|signalling"
)
ING_TAIL = re.compile(r",\s+(?:" + ING_WORDS + r")\b[^,;:]{3,}[.!]?$", re.I)

# 6. Inflated significance and stock AI phrasing
INFLATED = re.compile(
    r"\b(?:a testament to|testament to|plays? an? (?:crucial|vital|key|pivotal|important|essential|significant) role|"
    r"pivotal|game[- ]changer|game[- ]changing|serves as an?|stands as an?|cannot be overstated|can't be overstated|"
    r"truly remarkable|unparalleled|breathe(?:s)? new life|elevate(?:s)? your|transform(?:s)? your (?:space|home|garden|room)|"
    r"delve|delving|tapestry|in today's (?:fast-paced )?world|whether you're a seasoned|navigate the (?:world|complexities)|"
    r"a world of|unlock(?:s|ing)? the|the secret to|treasure trove|labor of love|labour of love|look no further|"
    r"the possibilities are endless|take (?:your|it) to the next level)\b",
    re.I,
)

# 7. Sales words (reported per word so a single common word can be judged)
SALES = re.compile(
    r"\b(?:stunning|stunningly|gorgeous|breathtaking|magical|must-have|effortless|effortlessly|"
    r"nestled|show-?stopp(?:er|ing)|eye-catching|jaw-dropping|unforgettable|vibrant|perfect|perfectly)\b",
    re.I,
)

# 8. Arguing with no one
NO_ONE = re.compile(
    r"^(?:but\s+)?(?:(?:this|that|it)\s+(?:isn't|is not|won't be)\s+(?:about|perfect|magic|rocket science|as hard|as complicated|a death sentence)\b|"
    r"none of this means|that's not to say|this is not to say|don't get me wrong|"
    r"(?:it|this|that)\s+(?:isn't|is not)\s+(?:just|only)\b)",
    re.I,
)

# 3. Closers: stock sign-offs and "Ready to...?"
SIGNOFF = re.compile(
    r"^(?:happy (?:planting|gardening|growing|potting)|enjoy|you've got this|you got this|let's (?:dive in|get started|get growing|dig in)|"
    r"here's to\b|ready to\b[^?]*\?|and that's it|that's it|trust me|the result\?|the best part\?|simple as that|easy as that)",
    re.I,
)
VERBISH = re.compile(
    r"\b(?:is|are|was|were|be|been|being|am|do|does|did|has|have|had|can|could|will|would|should|shall|may|might|must|"
    r"get|gets|got|go|goes|keep|keeps|need|needs|make|makes|let|grow|grows|water|check|try|use|add|give|put|take|see|look|"
    r"works?|helps?|thrives?|loves?|likes?|wants?|says?|knows?|feels?|seems?)\b|'s\b|'re\b|'ll\b|'ve\b|n't\b|\w+ed\b",
    re.I,
)

# 2 (review only). Possible forced triads
TRIAD_ADJ = re.compile(
    r"\b(?:is|are|was|were|be|feels?|looks?|stays?|remains?|becomes?|seems?|grows?|makes? (?:it|them|your \w+)|keeps? (?:it|them|your \w+))\s+"
    r"(?:\w+ly\s+)?([a-z][\w-]+),\s+([a-z][\w-]+),?\s+and\s+([a-z][\w-]+)\b",
    re.I,
)
TRIAD_CLAUSE = re.compile(
    r"\b(it|they|you|this|we)\s+\w+[^,.;]{0,40},\s+(?:\1\s+\w+[^,.;]{0,40},\s+and\s+\1\s+\w+|\w+[^,.;]{0,40},\s+and\s+\1\s+\w+)",
    re.I,
)
TRIAD_NOUNS = re.compile(r"\b([a-z]+(?:ness|ity|ance|ence|ment|ion|ism))\b,\s+([a-z]+(?:ness|ity|ance|ence|ment|ion|ism))\b,?\s+and\s+([a-z]+(?:ness|ity|ance|ence|ment|ion|ism))\b", re.I)


def sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    return [s.strip() for s in SENT_SPLIT.split(text) if s.strip()]


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9'’-]+", text))


def check_block(text, kind="p"):
    """kind: 'p' paragraph, 'li' list item, 'h' heading."""
    out = []
    sents = sentences(text)
    for i, s in enumerate(sents):
        plain = s.replace("’", "'").replace("‘", "'")
        for pat in NOT_X_PATTERNS:
            m = pat.search(plain)
            if m:
                out.append(("not_x_but_y", s, m.group(0)))
                break
        m = ING_TAIL.search(plain)
        if m:
            out.append(("ing_tail", s, m.group(0)))
        for m in INFLATED.finditer(plain):
            out.append(("inflated", s, m.group(0)))
        for m in SALES.finditer(plain):
            if re.search(r"\b(?:not|isn't|aren't|never)\s+(?:\w+\s+)?$", plain[:m.start()], re.I):
                continue  # "isn't perfect" is a denial, not a sales word
            word = m.group(0).lower()
            out.append(("soft_sales" if word in ("perfect", "perfectly") else "sales", s, word))
        m = NO_ONE.search(plain)
        if m:
            out.append(("no_one", s, m.group(0)))
        m = SIGNOFF.search(plain)
        if m:
            out.append(("closer", s, m.group(0)))
        elif kind == "p" and len(sents) >= 2 and i == len(sents) - 1:
            # Short paragraph-final line with no verb: a slogan fragment
            if word_count(plain) <= 4 and not VERBISH.search(plain) and plain[-1:] in ".!":
                out.append(("closer", s, s))
        for pat in (TRIAD_ADJ, TRIAD_NOUNS, TRIAD_CLAUSE):
            m = pat.search(plain)
            if m:
                out.append(("triad", s, m.group(0)))
                break
    # "No X. No Y." runs: two or more short sentences in a row starting with "No"
    run = 0
    for s in sents:
        if re.match(r"^No\s", s) and word_count(s) <= 6:
            run += 1
            if run == 2 and not any(f[0] == "closer" and f[1] == s for f in out):
                out.append(("closer", s, "No X. No Y. run"))
        else:
            run = 0
    return out
