"""Deterministic natural-language utilities.

These are shared by the offline provider, the router's fallback path, the
lexical retriever and the verifier. They are pure functions with no I/O so they
can be unit tested exhaustively.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

STOPWORDS = frozenset("""
a about above after again against all am an and any are aren't as at be because been before
being below between both but by can cannot could couldn't did didn't do does doesn't doing
don't down during each few for from further had hadn't has hasn't have haven't having he her
here hers herself him himself his how i if in into is isn't it its itself let's me more most
mustn't my myself no nor not of off on once only or other ought our ours ourselves out over
own same shan't she should shouldn't so some such than that the their theirs them themselves
then there these they this those through to too under until up very was wasn't we were weren't
what when where which while who whom why with won't would wouldn't you your yours yourself
yourselves please tell me know want need would like just
""".split())

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_'-]*")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n{2,}")

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "ninety": 90, "hundred": 100,
}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return re.sub(r"\s+", " ", text).strip()


def lower_words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def content_words(text: str) -> list[str]:
    return [w for w in lower_words(text) if w not in STOPWORDS and len(w) > 1]


def term_counts(text: str) -> Counter[str]:
    return Counter(content_words(text))


def sentences(text: str) -> list[str]:
    parts = [normalize(s) for s in _SENTENCE_RE.split(text or "")]
    return [p for p in parts if p]


def jaccard(a: str, b: str) -> float:
    sa, sb = set(content_words(a)), set(content_words(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def coverage(query: str, document: str) -> float:
    """Fraction of the query's content words that appear in the document."""
    q = set(content_words(query))
    if not q:
        return 0.0
    d = set(content_words(document))
    return len(q & d) / len(q)


def weighted_overlap(query: str, document: str) -> float:
    """Coverage weighted towards rarer (longer) terms; cheap idf substitute."""
    q = content_words(query)
    if not q:
        return 0.0
    d = set(content_words(document))
    total = sum(len(w) for w in set(q))
    if total == 0:
        return 0.0
    hit = sum(len(w) for w in set(q) if w in d)
    return hit / total


def extract_numbers(text: str) -> list[float]:
    out: list[float] = []
    for token in re.findall(r"-?\d+(?:\.\d+)?", text or ""):
        try:
            out.append(float(token))
        except ValueError:
            continue
    for word, value in NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", (text or "").lower()):
            out.append(float(value))
    return out


# ---------------------------------------------------------------------------
# Question shape detection
# ---------------------------------------------------------------------------

_ELIGIBILITY = re.compile(
    r"\b(am i|are we|is (he|she|they)|do i|does .* (qualify|entitle)|eligible|eligibility|"
    r"entitled|qualify|allowed to take|can i (take|claim|get))\b", re.I)
_QUANTITY = re.compile(
    r"\b(how many|how much|what is my|what's my|how long|number of|days? (of|am i)|amount)\b",
    re.I)
_DOCUMENT_LOOKUP = re.compile(
    r"\b(handbook|policy|document|manual|guide|spec|specification|according to|"
    r"what does .* say|states?|section|clause|per the)\b", re.I)
_CONTRADICTION = re.compile(
    r"\b(contradict|conflict|disagree|inconsist|which (one )?is correct|"
    r"differ(ent|s)?|mismatch|discrepan)\b", re.I)
_AUTHORIZATION = re.compile(
    r"\b(can (this |the )?user (access|read|view)|is .* (authori[sz]ed|permitted|allowed) to|"
    r"who can (access|see|read)|do i have (access|permission))\b", re.I)
_DEFINITION = re.compile(r"^\s*(what|who) (is|are|was|were)\s+(a |an |the )?([\w\s\-+.#]{2,60})\??\s*$",
                         re.I)
_GREETING = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|"
                       r"bye|goodbye)\b", re.I)

# Predicate vocabulary the demo domain uses. Extended at ingest time by adapters.
PREDICATE_HINTS: dict[str, tuple[str, ...]] = {
    "annual_leave_days": ("annual leave", "leave days", "vacation", "holiday allowance",
                          "paid leave", "leave entitlement", "leave allowance"),
    "years_of_service": ("years of service", "tenure", "service years", "worked here",
                         "length of service"),
    "sick_leave_days": ("sick leave", "sick days", "medical leave"),
    "remote_work_days": ("remote work", "work from home", "wfh", "hybrid days"),
    "probation_months": ("probation", "probationary period"),
    "notice_period_days": ("notice period", "resignation notice"),
    "training_budget": ("training budget", "learning budget", "development budget"),
    "expense_limit": ("expense limit", "expense cap", "reimbursement limit"),
    "salary": ("salary", "compensation", "pay", "remuneration"),
    "warranty_months": ("warranty", "guarantee period"),
    "return_window_days": ("return window", "return period", "returns policy"),
    "support_hours": ("support hours", "sla", "response time"),
}


def guess_predicate(text: str) -> str | None:
    low = normalize(text).lower()
    best: tuple[int, str] | None = None
    for predicate, hints in PREDICATE_HINTS.items():
        for hint in hints:
            if hint in low:
                score = len(hint)
                if best is None or score > best[0]:
                    best = (score, predicate)
    return best[1] if best else None


def question_shape(text: str) -> str:
    """Classify a question into a coarse shape used by the router."""
    t = normalize(text)
    if not t:
        return "empty"
    if _GREETING.match(t) and len(t) < 40:
        return "smalltalk"
    if _AUTHORIZATION.search(t):
        return "authorization"
    if _CONTRADICTION.search(t):
        return "contradiction"
    if _ELIGIBILITY.search(t):
        return "eligibility"
    if _QUANTITY.search(t):
        return "quantity"
    if _DOCUMENT_LOOKUP.search(t):
        return "document_lookup"
    if _DEFINITION.match(t):
        return "definition"
    return "open"


def definition_subject(text: str) -> str | None:
    m = _DEFINITION.match(normalize(text))
    if not m:
        return None
    return m.group(4).strip().rstrip("?").strip() or None


def mentions_first_person(text: str) -> bool:
    return bool(re.search(r"\b(i|me|my|mine|am i|do i)\b", (text or "").lower()))


def desired_value(text: str) -> float | None:
    """The number a yes/no eligibility question is asking about, if any."""
    nums = extract_numbers(text)
    return nums[0] if nums else None


def title_from(text: str, max_words: int = 8) -> str:
    words = normalize(text).split()
    if not words:
        return "New conversation"
    title = " ".join(words[:max_words])
    title = title.rstrip(" ?.!,;:")
    return (title[:1].upper() + title[1:])[:300] or "New conversation"
