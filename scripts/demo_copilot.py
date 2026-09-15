"""The final demonstration: one real support problem, four systems, and a false premise.

    python scripts/demo_copilot.py                      # offline systems
    LLM_PROVIDER=openai LLM_BASE_URL=... LLM_API_KEY=... LLM_MODEL=... python scripts/demo_copilot.py
    LLM_PROVIDER=local python scripts/demo_copilot.py   # open-weights model on this machine

Writes docs/demo/DEMO.md from what the systems actually answered. A system that cannot run
(no model configured) is recorded as not run - never simulated.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402
from app.evaluation import ns_benchmark as bench  # noqa: E402
from app.hybrid.controller import NeuroSymbolicController  # noqa: E402
from app.hybrid.knowledge_pack import load_pack  # noqa: E402
from app.neuro.providers.mock import MockLLMProvider  # noqa: E402

USER = "support@meridian.test"
ROLES = ["support"]
QUESTIONS = [
    ("The demonstration question",
     "My customer's laptop arrived damaged 12 days ago and they want a replacement. What can I offer?"),
    ("The hallucination test: a false premise",
     "The replacement policy is 30 days. My customer's laptop arrived damaged 12 days ago and they "
     "want a replacement. What can I offer?"),
]
TRUTH = ("Under Returns policy version 2 (in force from 1 Sep 2026, so it governs an item delivered "
         "12 days before 14 Sep 2026) damaged items must be reported within **15 days**, not 30. The "
         "laptop is in stock, so the playbook's first remedy is a **replacement**; the ₹62,999 order "
         "is above the ₹25,000 threshold, so **team-lead approval** is required first (SUP-2).")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    pack = load_pack(None, "semantic")
    systems = {
        "LLM only": bench.llm_only_system(pack),
        "LLM + RAG": bench.llm_rag_system(pack),
        "LLM + RAG + basic rules": bench.llm_rag_rules_system(pack),
    }
    praxis = NeuroSymbolicController(pack, llm=MockLLMProvider("praxis-mock-1"))
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    model = settings.llm_provider if settings.llm_provider != "mock" else "none configured"

    lines = ["# Final demonstration: one support problem, four systems", "",
             f"Generated {stamp} by `scripts/demo_copilot.py` from actual runs. Language model for "
             f"the baselines: **{model}**. Asked as `{USER}` (support role).", "",
             "**What the policy actually implies.** " + TRUTH, ""]
    for title, question in QUESTIONS:
        lines += [f"## {title}", "", f"> {question}", ""]
        case = {"question": question, "user": USER, "roles": ROLES}
        for name, system in systems.items():
            lines += [f"### {name}", ""]
            if system is None:
                lines += ["*Not run: no language model is configured (`LLM_PROVIDER=mock`). "
                          "It is not simulated.*", ""]
                continue
            answer = system(case)
            lines += ["```text", answer.text.strip() or answer.raw.strip(), "```",
                      f"Decision field: `{answer.decision}` · value: `{answer.value}` · "
                      f"outcome: `{answer.outcome}`", ""]
        result = praxis.answer(question, actor_email=USER, actor_roles=tuple(ROLES))
        trace = result.trace
        lines += ["### Praxis (neuro-symbolic)", "", "```text", result.answer, "```", "",
                  f"- **Decision:** {result.decision_label}",
                  f"- **Recommended action:** {result.recommended_action}",
                  f"- **Verification:** {result.verification.get('status')} "
                  f"({len(result.verification.get('checks', []))} checks)",
                  f"- **Confidence:** {result.confidence:.2f}",
                  "- **Policy version:** " + "; ".join(
                      f"{v['name']} — {v['basis']}" for v in result.policy_versions),
                  "- **Premise check:** " + ("; ".join(
                      f"claimed {c['stated']}, {c['reason']}" for c in result.claims
                      if not c["confirmed"]) or "no claim about the policy"),
                  "- **Rules applied:**"]
        lines += [f"  - `{r['rule']}` {r['name']} [{', '.join(r['clauses'])}]: `{r['formula']}`"
                  for r in result.rules_applied]
        lines += ["- **The ten steps, as recorded in the trace:**",
                  f"  1. Neural interpretation — request type "
                  f"`{trace['neural_interpretation']['speech_act']['label']}`, goal "
                  f"`{result.goal}`, stated inputs "
                  + ", ".join(f"`{i['predicate']}={i['value']}`"
                              for i in trace["neural_interpretation"]["stated_inputs"]),
                  f"  2. Entity resolution — {trace['grounding'].get('subjects')} "
                  f"(general case: {trace['grounding'].get('hypothetical')})",
                  f"  3. Authorized data retrieval — `{trace['authorization']['access']}` access, "
                  f"{trace['authorization']['withheld_records']} records withheld from the reasoner",
                  "  4. Knowledge-graph traversal — "
                  + ", ".join(sorted({f"{f['predicate']}" for f in trace["symbolic_facts"]})),
                  "  5. Policy version resolution — "
                  + "; ".join(v["name"] for v in trace["policy_versions"]),
                  f"  6. Symbolic rule evaluation — {len(trace['rules_used'])} rules applied, "
                  f"{len(trace['rules_considered'])} refuted",
                  f"  7. Deterministic decision — `{result.value}`",
                  "  8. Verification — " + ", ".join(
                      f"{c['check']} {'✓' if c['passed'] else '✕'}"
                      for c in trace["verification"]["checks"]),
                  f"  9. Evidence — " + ", ".join(
                      f"{s['id']}" + (f" v{s['version']}" if s.get("version") else "")
                      for s in result.sources),
                  "  10. Natural-language explanation — the answer above, composed only from the "
                  "verified conclusion", ""]
    out = ROOT / "docs" / "demo" / "DEMO.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
