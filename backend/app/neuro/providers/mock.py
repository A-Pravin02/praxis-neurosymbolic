"""Deterministic offline provider.

This is not a random stub. It is a rule-based language component that:

* parses a question into a structured intent frame,
* composes a grounded answer *only* from the approved evidence packet it is given,
* answers a small set of general-knowledge questions from a built-in glossary,
* and otherwise says plainly that it has no grounded information.

Because it cannot invent facts, running the platform with this provider isolates
the contribution of the architecture from the contribution of a large model. Swap
in `openai`/`anthropic`/`ollama` to get fluent prose over the same evidence packet.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from app.neuro.base import BaseProvider, Completion, CompletionRequest, Role, Task, estimate_tokens
from app.neuro.heuristics import (
    definition_subject, desired_value, guess_predicate, mentions_first_person, normalize,
    question_shape, sentences, title_from,
)

# A deliberately small general-knowledge glossary. It exists so the assistant can
# hold an ordinary conversation offline. Answers from here are labelled
# MODEL_GENERATED and are never presented as dataset facts.
GLOSSARY: dict[str, str] = {
    "python": (
        "Python is a high-level, general-purpose programming language known for its "
        "readable syntax and large standard library. It is dynamically typed, "
        "garbage-collected, and supports procedural, object-oriented and functional "
        "styles. It is widely used for web backends, data analysis, automation, "
        "scientific computing and machine learning."
    ),
    "sql": (
        "SQL (Structured Query Language) is the standard language for defining and "
        "querying relational databases. It expresses set-oriented operations such as "
        "SELECT, INSERT, UPDATE and DELETE over tables."
    ),
    "rag": (
        "Retrieval-augmented generation (RAG) is a pattern where a language model is "
        "given passages retrieved from a document store at query time, so its answer "
        "can be grounded in and cited against specific sources rather than relying "
        "only on parameters learned during training."
    ),
    "retrieval-augmented generation": (
        "Retrieval-augmented generation retrieves relevant passages from a corpus and "
        "supplies them to a language model as evidence, so answers can be attributed "
        "to sources."
    ),
    "machine learning": (
        "Machine learning is the study of algorithms that improve their performance on "
        "a task by fitting parameters to data rather than by being explicitly "
        "programmed with rules."
    ),
    "docker": (
        "Docker packages an application together with its dependencies into an image "
        "that runs as an isolated container, so the same artifact behaves consistently "
        "across machines."
    ),
    "postgresql": (
        "PostgreSQL is an open-source relational database known for standards "
        "compliance, extensibility and features such as row level security, JSONB "
        "storage and extensions like pgvector for similarity search."
    ),
    "row level security": (
        "Row level security is a database feature that attaches a predicate to a table "
        "so each session can only see or modify the rows that predicate admits. It "
        "enforces isolation in the database rather than in application code."
    ),
    "hallucination": (
        "In language models, a hallucination is fluent output that asserts something "
        "not supported by the model's inputs or by any verifiable source. It is a "
        "property of unconstrained generation, not a bug in a specific prompt."
    ),
    "symbolic reasoning": (
        "Symbolic reasoning applies explicit rules to explicit facts to derive new "
        "facts. Because every step is inspectable, conclusions can be replayed and "
        "audited, which is not true of a neural network's internal computation."
    ),
    "forward chaining": (
        "Forward chaining starts from known facts and repeatedly applies rules whose "
        "conditions are satisfied, adding the resulting conclusions, until no new "
        "facts can be derived."
    ),
    "backward chaining": (
        "Backward chaining starts from a goal and searches for rules whose conclusion "
        "matches it, then tries to establish those rules' premises, recursing until it "
        "reaches known facts."
    ),
    "fastapi": (
        "FastAPI is a Python web framework that derives request validation and OpenAPI "
        "documentation from type annotations, built on Starlette and Pydantic."
    ),
    "react": (
        "React is a JavaScript library for building user interfaces from composable "
        "components whose rendered output is a function of their state and props."
    ),
    "jwt": (
        "A JSON Web Token is a signed, base64url-encoded set of claims. The signature "
        "lets a recipient verify the claims were issued by a trusted party without a "
        "database lookup; it does not make the contents secret."
    ),
    "vector database": (
        "A vector database stores high-dimensional embeddings and answers nearest-"
        "neighbour queries over them, which is how semantic retrieval finds passages "
        "that are similar in meaning rather than in wording."
    ),
    "embedding": (
        "An embedding maps text (or another input) to a fixed-length numeric vector so "
        "that semantically similar inputs land close together under a distance measure "
        "such as cosine similarity."
    ),
}

ABSTENTION_TEXT = (
    "I don't have enough information in the available knowledge base to answer that "
    "reliably. I would rather say so than guess."
)


class MockLLMProvider(BaseProvider):
    name = "mock"

    def __init__(self, model: str = "praxis-mock-1") -> None:
        self.model = model

    # -- public API --------------------------------------------------------
    def complete(self, request: CompletionRequest) -> Completion:
        text = self._dispatch(request)
        prompt_chars = sum(len(m.content) for m in request.messages) + len(request.system)
        return Completion(
            text=text, model=self.model, provider=self.name,
            prompt_tokens=estimate_tokens("x" * prompt_chars),
            completion_tokens=estimate_tokens(text),
        )

    def stream(self, request: CompletionRequest) -> Iterator[str]:
        return super().stream(request)

    # -- dispatch ----------------------------------------------------------
    def _dispatch(self, request: CompletionRequest) -> str:
        question = self._last_user_message(request)
        ctx = request.context or {}
        if request.task is Task.INTENT:
            return json.dumps(build_intent_frame(question, ctx), separators=(",", ":"))
        if request.task is Task.TITLE:
            return title_from(question)
        if request.task is Task.CLAIM_SPLIT:
            return json.dumps(sentences(ctx.get("text", question)))
        if request.task is Task.ABSTAIN:
            return compose_abstention(ctx)
        if request.task is Task.GROUNDED_ANSWER:
            return compose_grounded_answer(question, ctx)
        if request.task is Task.EXPLAIN_INFERENCE:
            return compose_inference_explanation(ctx)
        return compose_direct_answer(question)

    @staticmethod
    def _last_user_message(request: CompletionRequest) -> str:
        for message in reversed(request.messages):
            if message.role is Role.USER:
                return message.content
        return request.messages[-1].content if request.messages else ""


# ---------------------------------------------------------------------------
# Intent frame
# ---------------------------------------------------------------------------

def build_intent_frame(question: str, ctx: dict | None = None) -> dict:
    ctx = ctx or {}
    shape = question_shape(question)
    predicate = guess_predicate(question)
    subject = "self" if mentions_first_person(question) else None
    intent = {
        "smalltalk": "smalltalk",
        "definition": "definition",
        "eligibility": "eligibility_check",
        "quantity": "attribute_lookup",
        "document_lookup": "document_lookup",
        "contradiction": "contradiction_analysis",
        "authorization": "policy_decision",
        "open": "open_question",
        "empty": "smalltalk",
    }[shape]
    # "What is the return window for the X1?" is shaped like a definition but is
    # really a lookup of a known attribute. Route it accordingly, or the pipeline
    # answers a workspace question as if it were general knowledge.
    if intent == "definition" and predicate:
        intent = "attribute_lookup"
    frame = {
        "intent": intent,
        "shape": shape,
        "entity": subject or (definition_subject(question) if shape == "definition" else None),
        "target_attribute": predicate,
        "desired_value": desired_value(question) if shape == "eligibility" else None,
        "requires_evidence": intent not in {"smalltalk", "definition", "open_question"},
        "keywords": sorted(set(re.findall(r"[a-z][a-z\-]{2,}", (question or "").lower())))[:20],
    }
    return frame


# ---------------------------------------------------------------------------
# Answer composition
# ---------------------------------------------------------------------------

def _fmt(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _fmt(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _cite(indexes: list[int]) -> str:
    if not indexes:
        return ""
    return " " + "".join(f"[{i}]" for i in sorted(set(indexes)))


def compose_grounded_answer(question: str, ctx: dict) -> str:
    claims: list[dict] = ctx.get("claims") or []
    conclusion: str = (ctx.get("conclusion") or "").strip()
    inferences: list[dict] = ctx.get("inferences") or []
    contradictions: list[dict] = ctx.get("contradictions") or []
    citations: list[dict] = ctx.get("citations") or []
    caveat: bool = bool(ctx.get("caveat"))

    lines: list[str] = []

    if conclusion:
        lines.append(conclusion if conclusion.endswith((".", "!", "?")) else conclusion + ".")

    supported = [c for c in claims if c.get("status") in {"supported", "verified"}]
    for claim in supported[:6]:
        text = normalize(claim.get("text", ""))
        if not text:
            continue
        if conclusion and text.lower().rstrip(".") == conclusion.lower().rstrip("."):
            continue
        marks = _cite(claim.get("citation_indexes") or [])
        lines.append(f"{text.rstrip('.')}.{marks}" if marks else f"{text.rstrip('.')}.")

    if inferences:
        steps = "; ".join(
            f"{normalize(step.get('sentence') or step.get('conclusion', ''))} "
            f"(rule {step.get('rule_code', '?')})"
            for step in inferences[:4]
        )
        lines.append(f"This follows from the recorded facts and rules: {steps}.")

    if contradictions:
        for c in contradictions[:2]:
            values = ", ".join(_fmt(v) for v in c.get("values", []))
            marks = _cite(c.get("citation_indexes") or [])
            label = str(c.get("predicate", "this value")).replace("_", " ")
            if c.get("resolved"):
                lines.append(
                    f"The sources disagree on the {label}: recorded values are "
                    f"{values}.{marks} {c.get('resolution', '')} The superseded value is "
                    f"shown so the discrepancy is visible rather than hidden."
                )
            else:
                lines.append(
                    f"Note: the sources disagree on the {label} for "
                    f"{c.get('subject', 'this subject')}; recorded values are "
                    f"{values}.{marks} I have not resolved which is authoritative."
                )

    if not lines and citations:
        # Quarantined passages are evidence to look at, never text to repeat as an
        # answer: a document trying to issue instructions must not get a voice.
        usable = [c for c in citations if not c.get("quarantined")]
        if usable:
            top = usable[0]
            snippet = normalize(top.get("snippet", ""))[:400]
            if snippet:
                lines.append(
                    f"According to {top.get('title', 'the source')}: "
                    f"\"{snippet}\" [{top.get('index', 1)}]"
                )

    if caveat:
        lines.append(
            "Treat this as provisional: the supporting evidence is thinner than the "
            "threshold I use for a confident answer."
        )

    if not lines:
        return ABSTENTION_TEXT
    return "\n\n".join(lines)


def compose_abstention(ctx: dict) -> str:
    reasons: list[str] = ctx.get("reasons") or []
    text = ABSTENTION_TEXT
    if reasons:
        text += "\n\nWhy: " + "; ".join(_fragment(r) for r in reasons[:4]) + "."
    suggestions = ctx.get("suggestions") or []
    if suggestions:
        text += "\n\nYou could try: " + "; ".join(
            _fragment(s) for s in suggestions[:3]) + "."
    return text


def _fragment(text: str) -> str:
    """Strip terminal punctuation so joined fragments do not double up."""
    return normalize(text).rstrip(" .;")


def compose_inference_explanation(ctx: dict) -> str:
    steps: list[dict] = ctx.get("inferences") or []
    if not steps:
        return "No inference steps were required."
    parts = []
    for i, step in enumerate(steps, start=1):
        premises = ", ".join(step.get("premises", []))
        parts.append(
            f"{i}. Rule {step.get('rule_code', '?')}"
            f"{' (' + step['rule_name'] + ')' if step.get('rule_name') else ''}: "
            f"given {premises or 'the recorded facts'}, therefore "
            f"{step.get('conclusion', '')}."
        )
    return "\n".join(parts)


def compose_direct_answer(question: str) -> str:
    subject = definition_subject(question)
    key = (subject or normalize(question)).lower().strip(" ?.")
    for candidate in (key, key.rstrip("s"), key.replace("-", " ")):
        if candidate in GLOSSARY:
            return GLOSSARY[candidate]
    # Substring match for phrases like "what is a vector database used for"
    for term, definition in GLOSSARY.items():
        if term in normalize(question).lower():
            return definition

    shape = question_shape(question)
    if shape == "smalltalk":
        return (
            "Hello. I'm Praxis, an assistant that answers from your workspace's documents "
            "and rules. Ask me something about them and I'll show you the evidence and the "
            "reasoning behind my answer - or tell you plainly when I don't have enough to "
            "go on."
        )
    return (
        "I don't have a grounded answer for that. This looks like general knowledge rather "
        "than something in your workspace's knowledge base, and the offline model I'm "
        "running has only a small built-in glossary. Configure a cloud or local model "
        "provider for open-domain answers, or ask me something covered by your documents."
    )
