"""NEURAL (with symbolic validation). Find the things a question mentions.

Four kinds of mention:

* **record references** - "order 12345", "#12346", "CLM-3001". Pattern-extracted and
  then *validated against the knowledge graph*: a number that is not a real record is
  not treated as one.
* **quantities** - "₹8,750", "450 km", "two weeks ago", "25 days". Typed by unit, so a
  later step can bind a money amount to a money input and a duration to a days input.
* **products and categories** - linked to graph entities by name, falling back to
  semantic similarity for words the catalogue does not use ("headphones" -> earbuds).
* **discourse cues** - first person, a deictic "it"/"this" that needs a referent, a
  hypothetical "if", a comparison.

Nothing here decides what the question means; it only finds what is in it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.neural.encoder import Encoder
    from app.symbolic.knowledge_graph import KnowledgeGraph

_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "couple": 2, "couple of": 2, "few": 3,
}
_UNIT_DAYS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "fortnight": 14, "fortnights": 14,
              "month": 30, "months": 30}
_NUM = r"(\d[\d,]*(?:\.\d+)?)"
_WORD_NUM = r"(" + "|".join(sorted((k for k in _NUMBER_WORDS if " " not in k),
                                   key=len, reverse=True)) + r")"

_MONEY_PATTERNS = [
    re.compile(r"(?:₹|\brs\.?|\binr)\s*" + _NUM, re.IGNORECASE),
    re.compile(_NUM + r"\s*(?:rupees?|rs\b|inr\b|₹)", re.IGNORECASE),
]
_MONEY_CONTEXT = re.compile(
    r"\b(?:cost|costs|spent|spend|paid|pay|price|priced|amount|claim(?:ed)?|bill|total|"
    r"worth|expense|expenses)\b[^\d]{0,12}" + _NUM, re.IGNORECASE)
_DISTANCE = re.compile(_NUM + r"\s*(?:km|kms|kilomet(?:er|re)s?)\b", re.IGNORECASE)
_DURATION_NUM = re.compile(_NUM + r"\s*(days?|weeks?|months?|fortnights?)\b", re.IGNORECASE)
_DURATION_WORD = re.compile(r"\b" + _WORD_NUM + r"\s+(days?|weeks?|months?|fortnights?)\b",
                            re.IGNORECASE)
_FORTNIGHT = re.compile(r"\ba\s+fortnight\b", re.IGNORECASE)
_YESTERDAY = re.compile(r"\byesterday\b", re.IGNORECASE)
_RECORD_ID = re.compile(r"\b([A-Za-z]{3})-?(\d{3,6})\b")
_BARE_ID = re.compile(r"(?:#|\b(?:order|claim|return|refund|product|no\.?|number)\s*#?\s*)"
                      r"(\d{3,6})\b", re.IGNORECASE)

_PERSONAL = re.compile(r"\b(i|i'm|i've|me|my|mine|we|our|us)\b", re.IGNORECASE)
_DEICTIC = re.compile(r"\b(it|this|that|these|those|them)\b", re.IGNORECASE)
_HYPOTHETICAL = re.compile(r"\b(if|suppose|supposing|assuming|would|hypothetically|"
                           r"what happens)\b", re.IGNORECASE)
_COMPARISON = re.compile(r"\b(compare|comparison|versus|vs\.?|difference between|"
                         r"better than|which is better)\b", re.IGNORECASE)
_NEGATION = re.compile(r"\b(not|no|never|without|none|haven't|hasn't|didn't|don't|"
                       r"doesn't|isn't|wasn't|can't|cannot|won't|n't)\b", re.IGNORECASE)
_STOP = {"the", "a", "an", "my", "i", "is", "it", "to", "of", "for", "and", "or", "can",
         "in", "on", "this", "that", "was", "be", "me", "do", "does", "did", "what",
         "why", "how", "when", "where", "which", "who", "set", "order", "item", "product",
         "g", "l", "with", "have", "has", "get", "got"}


# Words that pack or size a product rather than name it; never a product's head noun.
_GENERIC_PRODUCT_WORDS = {"set", "pack", "kit", "pair", "box", "bundle", "combo"}


@dataclass
class Quantity:
    kind: str          # money | distance | days
    value: float
    text: str
    start: int


@dataclass
class EntityMention:
    entity_id: str
    entity_type: str
    text: str
    method: str        # id | name | semantic
    score: float = 1.0


@dataclass
class Mentions:
    records: list[EntityMention] = field(default_factory=list)
    products: list[EntityMention] = field(default_factory=list)
    categories: list[EntityMention] = field(default_factory=list)
    quantities: list[Quantity] = field(default_factory=list)
    unresolved_ids: list[str] = field(default_factory=list)
    personal: bool = False
    deictic: bool = False
    hypothetical: bool = False
    comparison: bool = False

    def as_dict(self) -> dict:
        return {
            "records": [vars(m) for m in self.records],
            "products": [vars(m) for m in self.products],
            "categories": [vars(m) for m in self.categories],
            "quantities": [vars(q) for q in self.quantities],
            "unresolved_ids": self.unresolved_ids,
            "cues": {"personal": self.personal, "deictic": self.deictic,
                     "hypothetical": self.hypothetical, "comparison": self.comparison},
        }


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def split_clauses(question: str) -> list[str]:
    """Clause-sized pieces, so one statement is not diluted by the rest of the question."""
    parts = re.split(r"(?<=[.?!;])\s+|(?<!\d),\s*|,\s+|\s+(?:but|and|because|so|although|though|"
                     r"even though|since|however)\s+|\s+(?=(?:if|when|as|after)\s)",
                     question.strip(), flags=re.IGNORECASE)
    clauses = []
    for part in parts:
        part = part.strip(" .?!")
        # "if they arrived damaged" states a condition of the case being asked about; as
        # a clause it is a statement, so its subordinator is removed. Left attached to
        # "Can I return the almonds", the whole sentence read as a question and the
        # damage was never registered.
        part = re.sub(r"^(?:if|when|as|after)\s+", "", part, flags=re.IGNORECASE)
        if part and len(part) > 2:
            clauses.append(part)
    return clauses or [question.strip()]


# A wh-word always opens a question. An auxiliary opens one only when a subject
# follows it: "can I...", "is it...", "have you..." ask; "have all the bills" states.
_INTERROGATIVE = re.compile(
    r"^\s*(?:(?:how|what|why|where|when|which|who|whom|whose)\b|"
    r"(?:can|could|may|might|am|is|are|was|were|will|would|shall|should|do|does|did|"
    r"have|has)\s+(?:i|you|we|they|he|she|it|my|your|our|their|the|this|that|these|"
    r"those|there|a|an|any)\b)",
    re.IGNORECASE)


def is_question_clause(clause: str) -> bool:
    """A clause that asks rather than states."""
    return bool(_INTERROGATIVE.match(clause))


def description_facets(description: str) -> list[str]:
    """The separate phrasings inside one schema description, plus the whole of it.

    "the product arrived broken, cracked, smashed, damaged, faulty or defective" yields
    the full sentence and its head phrase with each alternative substituted, so that a
    four-word clause is compared with a four-word phrase.
    """
    text = description.strip()
    facets = [text]
    parts = [p.strip() for p in re.split(r",\s*|\s+or\s+", text) if p.strip()]
    if len(parts) > 1:
        head_words = parts[0].split()
        # Alternatives that are single words or short phrases replace the last word of
        # the head phrase: "arrived broken" -> "arrived cracked", "arrived smashed".
        for alternative in parts[1:]:
            if len(alternative.split()) <= 3 and len(head_words) > 1:
                facets.append(" ".join(head_words[:-1] + [alternative]))
            else:
                facets.append(alternative)
        facets.append(parts[0])
    return list(dict.fromkeys(facets))


def negates_concept(clause: str, facet: str, *, window: int = 3) -> bool:
    """Whether a negation in the clause sits right before a word of the matched concept."""
    words = re.findall(r"[a-z']+", clause.lower())
    concept = {w[:5] for w in re.findall(r"[a-z]+", facet.lower()) if len(w) > 3}
    for index, word in enumerate(words):
        if _NEGATION.fullmatch(word) or word.endswith("n't"):
            following = words[index + 1:index + 1 + window]
            if any(w[:5] in concept for w in following if len(w) > 3):
                return True
    return False


def is_negated(clause: str) -> bool:
    return bool(_NEGATION.search(clause))


def extract_quantities(question: str, *, exclude_spans: list[tuple[int, int]] = ()) -> list[Quantity]:
    found: list[Quantity] = []
    taken: list[tuple[int, int]] = list(exclude_spans)

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    def add(kind: str, value: float, match: re.Match, group: int = 1) -> None:
        span = match.span(group)
        if free(span):
            taken.append(span)
            found.append(Quantity(kind, value, match.group(0).strip(), span[0]))

    for pattern in _MONEY_PATTERNS:
        for m in pattern.finditer(question):
            add("money", _number(m.group(1)), m)
    for m in _DISTANCE.finditer(question):
        add("distance", _number(m.group(1)), m)
    for m in _DURATION_NUM.finditer(question):
        add("days", _number(m.group(1)) * _UNIT_DAYS[m.group(2).lower()], m)
    for m in _DURATION_WORD.finditer(question):
        add("days", _NUMBER_WORDS[m.group(1).lower()] * _UNIT_DAYS[m.group(2).lower()], m)
    for m in _FORTNIGHT.finditer(question):
        add("days", 14, m, 0)
    for m in _YESTERDAY.finditer(question):
        add("days", 1, m, 0)
    for m in _MONEY_CONTEXT.finditer(question):
        add("money", _number(m.group(1)), m)
    found.sort(key=lambda q: q.start)
    return found


def extract_mentions(question: str, graph: "KnowledgeGraph",
                     encoder: "Encoder | None" = None) -> Mentions:
    mentions = Mentions(
        personal=bool(_PERSONAL.search(question)),
        deictic=bool(_DEICTIC.search(question)),
        hypothetical=bool(_HYPOTHETICAL.search(question)),
        comparison=bool(_COMPARISON.search(question)),
    )

    # -- record references, validated against the graph ----------------------
    id_spans: list[tuple[int, int]] = []
    by_digits: dict[str, list[str]] = {}
    for entity_id in graph.entities:
        m = re.match(r"^[A-Za-z]+-(\d+)$", entity_id)
        if m:
            by_digits.setdefault(m.group(1), []).append(entity_id)

    def link_digits(digits: str, text: str, span: tuple[int, int], prefix: str = "") -> None:
        candidates = by_digits.get(digits, [])
        if prefix:
            candidates = [c for c in candidates if c.upper().startswith(prefix.upper())]
        if len(candidates) == 1:
            entity = graph.entities[candidates[0]]
            if all(r.entity_id != entity.id for r in mentions.records):
                mentions.records.append(EntityMention(entity.id, entity.type, text, "id"))
            id_spans.append(span)
        elif not candidates:
            mentions.unresolved_ids.append(text)
            id_spans.append(span)

    for m in _RECORD_ID.finditer(question):
        link_digits(m.group(2), m.group(0), m.span(), prefix=m.group(1))
    for m in _BARE_ID.finditer(question):
        if all(m.span(1)[1] <= a or m.span(1)[0] >= b for a, b in id_spans):
            link_digits(m.group(1), m.group(0).strip(), m.span(1))

    mentions.quantities = extract_quantities(question, exclude_spans=id_spans)

    # -- products and categories ----------------------------------------------
    words = [w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in _STOP]
    word_set = set(words) | {w.rstrip("s") for w in words}
    for entity in graph.entities_of_type("product"):
        ordered = [w for w in re.findall(r"[a-z0-9.]+", entity.name.lower())
                   if w not in _STOP and not any(ch.isdigit() for ch in w) and len(w) > 2]
        name_words = set(ordered)
        head = next((w for w in reversed(ordered) if w not in _GENERIC_PRODUCT_WORDS), None)
        hits = {w for w in name_words if w in word_set or w.rstrip("s") in word_set}
        # One shared modifier is not a reference: "gift wrapping" is not the gift card.
        # A link needs the product's head noun ("card", "kettle", "bedsheet") or two of
        # its words.
        if hits and (len(hits) >= 2 or (head is not None and head in hits)):
            mentions.products.append(EntityMention(entity.id, "product", " ".join(sorted(hits)),
                                                   "name", len(hits) / len(name_words)))
    for entity in graph.entities_of_type("category"):
        name = entity.name.lower()
        variants = {name, name.rstrip("s"), entity.id.replace("_", " "),
                    entity.id.replace("_", " ").rstrip("s"), name + "s"}
        if any(re.search(rf"\b{re.escape(v)}\b", question.lower()) for v in variants if v):
            mentions.categories.append(EntityMention(entity.id, "category", entity.name, "name"))

    if not mentions.products and encoder is not None and encoder.semantic and words:
        _semantic_products(question, words, graph, encoder, mentions)

    mentions.products.sort(key=lambda m: -m.score)
    return mentions


def _semantic_products(question: str, words: list[str], graph: "KnowledgeGraph",
                       encoder: "Encoder", mentions: Mentions) -> None:
    """Link a product the question names in words the catalogue does not use."""
    products = graph.entities_of_type("product")
    if not products:
        return
    chunks = list(dict.fromkeys(words + [" ".join(p) for p in zip(words, words[1:])]))
    names = [p.name for p in products]
    scores = encoder.similarity(chunks, names)
    best_score = float(scores.max()) if scores.size else 0.0
    if best_score >= 0.80:
        row, col = divmod(int(scores.argmax()), scores.shape[1])
        mentions.products.append(EntityMention(products[col].id, "product", chunks[row],
                                               "semantic", round(best_score, 3)))
