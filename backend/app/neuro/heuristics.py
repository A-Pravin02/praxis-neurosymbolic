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


def stem(word: str) -> str:
    """Very light suffix stripping so "bands" matches "band".

    Deliberately crude: a full stemmer would need a dependency and would change
    behaviour across languages. This only collapses regular English plurals and
    a couple of common verb endings, which is what term-overlap scoring needs.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def content_words(text: str) -> list[str]:
    return [stem(w) for w in lower_words(text)
            if w not in STOPWORDS and len(w) > 1]


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


_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_IDENTIFIER = re.compile(r"\b[A-Za-z]{1,4}[-_]?\d{1,4}\b")
# Grouped digits are matched first so "423,325" is one number rather than 423 and
# 325. Each group must be exactly three digits, so a list like "1,2,3" is still
# read as three separate values.
_GROUPED_NUMBER = re.compile(r"(?<![\w.\-])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_NUMBER = re.compile(r"(?<![\w.\-])-?\d+(?:\.\d+)?")


def extract_numbers(text: str) -> list[float]:
    """Numeric values asserted by the text.

    Dates and identifiers (rule codes such as R12, employee ids such as E-1002)
    are stripped first: they are labels, not quantities, and scoring them as
    quantities makes the grounding check reject correct answers.

    Thousands separators are handled explicitly. Without that, a document saying
    "423,325 rows" yields 423 and 325, never matches a fact holding 423325, and
    the system abstains on something it can prove.
    """
    cleaned = _IDENTIFIER.sub(" ", _ISO_DATE.sub(" ", text or ""))
    out: list[float] = []
    for token in _GROUPED_NUMBER.findall(cleaned):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    # Blank the grouped matches so the plain pattern cannot re-read their parts.
    cleaned = _GROUPED_NUMBER.sub(" ", cleaned)
    for token in _NUMBER.findall(cleaned):
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
_ELIGIBILITY_OPENER = re.compile(
    r"^\s*(am i|are we|are they|is (he|she|they)|do i|does (he|she|they)|can i|may i)\b",
    re.I)
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
# Questions that ask to be told *about* something rather than asking for one
# specific fact. They have no single answering sentence, so scoring passages by
# how much of the question they cover - the right test for "what is the notice
# period" - discards every passage and answers nothing.
_EXPLORATORY = re.compile(
    r"\b(tell me about|what can you tell me|summar(y|ise|ize)|overview of|"
    r"describe|walk me through|what do you know about|what are the (new |current )?"
    r"(policies|policy|rules|guidelines|benefits|procedures)|"
    r"anything (else )?about|more about|brief me)\b",
    re.IGNORECASE)

_DEFINITION = re.compile(r"^\s*(what|who) (is|are|was|were)\s+(a |an |the )?([\w\s\-+.#]{2,60})\??\s*$",
                         re.I)
_GREETING = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|thanks|thank you|"
                       r"bye|goodbye)\b", re.I)

# Predicate vocabulary the demo domain uses. Extended at ingest time by adapters.
PREDICATE_HINTS: dict[str, tuple[str, ...]] = {
    # Hints must be specific enough to be unambiguous. "leave allowance" was
    # removed because it matches both annual and sick leave; every hint here has
    # to contain the word that distinguishes the predicate.
    "annual_leave_days": ("annual leave", "vacation days", "vacation allowance",
                          "holiday allowance", "paid leave", "annual leave entitlement",
                          "leave entitlement"),
    "years_of_service": ("years of service", "length of service", "service years",
                         "tenure"),
    "sick_leave_days": ("sick leave", "sick days", "medical leave"),
    "remote_work_days": ("remote work", "remote working days", "work from home", "wfh"),
    "probation_months": ("probationary period", "probation period"),
    "notice_period_days": ("notice period", "resignation notice"),
    "training_budget": ("training budget", "learning budget", "development budget"),
    "equipment_budget": ("equipment budget", "hardware budget"),
    "expense_limit": ("expense limit", "expense cap", "reimbursement limit"),
    "salary": ("salary", "compensation", "remuneration"),
    "warranty_months": ("warranty period", "warranty", "guarantee period"),
    "return_window_days": ("return window", "return period", "returns policy"),
    "support_hours": ("support hours",),
    "retention_years": ("data retention", "retention period"),
    "incident_report_hours": ("incident reporting", "incident report window"),
    "review_days": ("review cycle", "access review"),
}

# Readable noun phrases used when a fact is rendered as a sentence. The unit
# comes from the fact's stored value, so labels never repeat it.
PREDICATE_LABELS: dict[str, str] = {
    "annual_leave_days": "annual leave entitlement",
    "sick_leave_days": "sick leave allowance",
    "remote_work_days": "remote working allowance",
    "notice_period_days": "notice period",
    "carryover_days": "leave carryover limit",
    "probation_months": "probationary period",
    "years_of_service": "length of service",
    "training_budget": "training budget",
    "equipment_budget": "equipment budget",
    "expense_limit": "single-item expense limit",
    "warranty_months": "warranty period",
    "return_window_days": "return window",
    "support_hours": "support hours start time",
    "on_probation": "probation status",
    "retention_years": "data retention period",
    "incident_report_hours": "incident reporting deadline",
    "salary": "salary",
    "age": "age",
    "department": "department",
    "country": "country",
    "employee_id": "employee identifier",
}


def label_for(predicate: str) -> str:
    return PREDICATE_LABELS.get(predicate, predicate.replace("_", " "))


def phrase_fact(subject: str, predicate: str, value: str) -> str:
    """Render a triple as an English sentence for a claim or a conclusion."""
    label = label_for(predicate)
    if subject == "self":
        return f"Your {label} is {value}"
    if subject in ("policy", "company", "organisation"):
        return f"The {label} is {value}"
    return f"For {subject}, the {label} is {value}"


def guess_predicate(text: str) -> str | None:
    """Map a question onto a known predicate, preferring the longest hint match."""
    low = normalize(text).lower()
    best: tuple[int, str] | None = None
    for predicate, hints in PREDICATE_HINTS.items():
        for hint in hints:
            if hint in low and (best is None or len(hint) > best[0]):
                best = (len(hint), predicate)
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
    # "Am I entitled to 20 days?" is a yes/no eligibility question; "How many days
    # am I entitled to?" asks for a value. Both mention entitlement, so the opener
    # decides which one it is.
    if _ELIGIBILITY_OPENER.match(t):
        return "eligibility"
    if _QUANTITY.search(t):
        return "quantity"
    if _ELIGIBILITY.search(t):
        return "eligibility"
    if _DOCUMENT_LOOKUP.search(t):
        return "document_lookup"
    if _DEFINITION.match(t):
        return "definition"
    return "open"


def is_exploratory(text: str) -> bool:
    """True when the question asks for an account of a topic, not one value.

    The distinction matters to claim selection, not to trust: an exploratory
    answer is still assembled only from retrieved, cited sentences. What changes
    is that several passages may each be partly relevant, where a value question
    has exactly one sentence that answers it.
    """
    return bool(_EXPLORATORY.search(normalize(text) or ""))


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
