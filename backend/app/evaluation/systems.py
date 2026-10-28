"""Systems compared by the research benchmark, each built for one knowledge pack.

    neurosymbolic            the full system, offline: embedding understanding, answers
                             phrased by the deterministic realizer
    ns_parser                the full system with the language model as semantic parser
                             (the same model the LLM baselines use); decisions still symbolic
    ns_parser_llm            ns_parser, with answers also phrased by the model
    ns_parser_<ablation>     each ablation below, on the parser configuration
    neurosymbolic_llm        the full system; answers phrased by the language model, whose
                             wording is checked against the established conclusion
    neurosymbolic_lexical    ablation: semantic encoder replaced by lexical hashing
    ns_no_verification       ablation: answers are released even when verification fails
    ns_latest_version        ablation: the newest policy version is applied to every case
    ns_two_valued            ablation: an unknown fact is treated as false
    ns_trust_statements      ablation: facts a person states override company records and
                             claimed approvals count as recorded
    ns_no_authorization      ablation: reasoning sees every record, whoever asks
    rag_extractive           retrieval only: the retrieved passages are the answer
    llm_only                 the model alone, with the asker's identity and today's date
    llm_rag                  the model with retrieval over policies and authorized records
    llm_rag_selfcheck        llm_rag, then the model verifies its own draft against the same
                             context and revises it (chain-of-verification style)
    llm_rag_rules            the model extracts structure, a classical rule engine decides

A builder returns None when the system needs a language model and none is configured; the
benchmark then reports the system as not run and never simulates it.
"""
from __future__ import annotations

from typing import Callable

from app.evaluation import ns_benchmark as nb

ABLATIONS = {
    "ns_no_verification": {"no_verification"},
    "ns_latest_version": {"latest_version"},
    "ns_two_valued": {"two_valued"},
    "ns_trust_statements": {"trust_statements"},
    "ns_no_authorization": {"no_authorization"},
    "ns_no_number_check": {"no_number_check"},
    "ns_no_kind_agreement": {"no_kind_agreement"},
}


def _neurosymbolic(*, ablations: frozenset[str] = frozenset(), realizer: str = "template",
                   encoder: str | None = None, understanding: str = "encoder"):
    def build(pack):
        import time

        from app.hybrid.controller import NeuroSymbolicController
        from app.hybrid.knowledge_pack import load_pack
        from app.neuro.providers.mock import MockLLMProvider

        if encoder is not None:
            pack = load_pack(str(pack.directory), encoder)
        model = nb._llm() if "llm" in (realizer, understanding) else None
        if "llm" in (realizer, understanding) and model is None:
            return None
        llm = model if realizer == "llm" else MockLLMProvider("praxis-mock-1")
        controller = NeuroSymbolicController(pack, llm=llm, ablations=ablations,
                                             understanding=understanding)
        if understanding == "llm" and realizer != "llm":
            # The parser needs the model even when answers are phrased by templates.
            from app.neural.semantic_parser import LLMSemanticParser
            controller.understanding = LLMSemanticParser(
                pack.program, pack.graph, pack.policies, model, pack.understanding)

        def run(case: dict):
            started = time.perf_counter()
            answer = controller.answer(case["question"], actor_email=case["user"],
                                       actor_roles=tuple(case.get("roles", ())))
            interpretation = answer.trace.get("neural_interpretation", {})
            # The parser marks a fallback two ways: it renames the encoder, and sets
            # invalid_goal when the model named a goal outside the schema.
            fallback = None
            if understanding == "llm":
                fallback = ("fallback:" in str(interpretation.get("encoder", ""))
                            or bool((interpretation.get("parse") or {}).get("invalid_goal")))
            return nb.SystemAnswer(
                parser_fallback=fallback,
                text=answer.answer, outcome=answer.outcome, goal=answer.goal,
                pipeline=answer.pipeline, decision=answer.decision, value=answer.value,
                subject=answer.subject, sources=[s["id"] for s in answer.sources],
                verified=answer.trace.get("verification", {}).get("passed"),
                confidence=answer.confidence,
                latency_ms=(time.perf_counter() - started) * 1000)
        return run
    return build


def _selfcheck(pack):
    """Draft with retrieval, then verify the draft against the same context and revise."""
    import time

    llm = nb._llm()
    if llm is None:
        return None
    documents = nb._documents(pack)

    def run(case: dict):
        started = time.perf_counter()
        context = nb._retrieve(pack, documents, case, k=6)
        block = "\n".join(f"[{d['id']}] {d['text']}" for d in context)
        prompt = f"{nb._who(pack, case)}\n\nContext:\n{block}\n\nQuestion: {case['question']}"
        draft = nb._ask(llm, "You are a customer service and employee assistant. Answer using "
                             "only the context. " + nb._ANSWER_FORMAT, prompt)
        revised = nb._ask(
            llm,
            "You check a draft answer for errors before it is sent. Verify every claim in the "
            "draft against the context: the policy version that applies to the case's dates, "
            "every number, every condition, whether the person is allowed to see the record, "
            "whether the question gives every fact the policy needs, and whether any claim in "
            "the question itself is contradicted by the context. Then output the corrected "
            "answer. " + nb._ANSWER_FORMAT,
            f"{prompt}\n\nDraft answer:\n{draft}")
        answer = nb._from_json(revised, started, [d["id"] for d in context])
        answer.raw = f"DRAFT:\n{draft}\n\nREVISED:\n{revised}"
        return answer
    return run


BUILDERS: dict[str, Callable] = {
    "neurosymbolic": _neurosymbolic(),
    # Same model as the LLM baselines, used only to parse the question into the schema.
    "ns_parser": _neurosymbolic(understanding="llm"),
    "ns_parser_llm": _neurosymbolic(understanding="llm", realizer="llm"),
    **{f"ns_parser_{name[3:]}": _neurosymbolic(ablations=frozenset(flags), understanding="llm")
       for name, flags in ABLATIONS.items()},
    "neurosymbolic_llm": _neurosymbolic(realizer="llm"),
    # The verification gate exists to police prose the model wrote, and the default
    # configuration writes none, so removing it changes nothing there. This pair is
    # where the question can actually be asked: the model phrases the answer, and the
    # gate is the only thing standing between its phrasing and the reply.
    "neurosymbolic_llm_no_verification": _neurosymbolic(
        realizer="llm", ablations=frozenset({"no_verification"})),
    "neurosymbolic_lexical": _neurosymbolic(encoder="hashing"),
    **{name: _neurosymbolic(ablations=frozenset(flags)) for name, flags in ABLATIONS.items()},
    "rag_extractive": nb.rag_extractive_system,
    "llm_only": nb.llm_only_system,
    "llm_rag": nb.llm_rag_system,
    "llm_rag_selfcheck": _selfcheck,
    "llm_rag_rules": nb.llm_rag_rules_system,
}


def build_system(name: str) -> Callable:
    return BUILDERS[name]
