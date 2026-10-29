# Unresolved items

Generated 2026-10-28 09:59 UTC. Each entry states what is missing and what would settle it.

| Item | Status | What it would take |
|---|---|---|
| Verifier precision and recall | Unmeasured | The verifier is correct by construction with respect to its own graph. Independent ground-truth labels, authored without reference to the rule base, would be needed. |
| Question relevance, parser configuration | Implemented | A gate on the parser's "none" verdict: a policy clause may no longer answer over it when the question is about one person's case. Test split 6/15 to 15/15 correct, 9 gained and 0 lost; Meridian 0 lost. See `docs/audit/RELEVANCE_GATE.md`. |
| Question relevance, LLM-free configuration | Not implemented | The encoder selects a goal by nearest neighbour and so has no way to name none. Still 0 of 15 abstentions there. Needs negative catalogue entries that transfer, which means enumerating what the company does not do. |
| Relevance gate's dependence on model quality | Measured, and a real limit | The gate executes the parser's verdict, so it inherits the parser's errors. The 1.5B calls 29 of 86 covered Meridian questions out of scope. None of those was being answered correctly before, so nothing is lost — but a weak parser turns the gate into an abstention machine. Unmeasured for the 7B, which is not on this machine. |
| Model capability effect | Open question | The 1.5B and 7B runs used different inference stacks. One stack serving both sizes on the same split. |
| Independently authored evaluation | Not done | A second person writing questions and gold answers without seeing the rule base. |
| Conference reference pagination | Partly verified | Pages and DOIs confirmed for 5 journal and 5 conference entries. Self-RAG, Program-of-Thoughts and Binder publish through OpenReview without page numbers; the NeurIPS entry's volume is confirmed but its page range is not. |
| Prose-faithfulness metric | Not implemented | Needed to quantify the gate's contribution beyond counting changed answers and unsupported figures. |
| Run-to-run variance | Estimated from two runs | Three to five seeded repeats would give a variance estimate rather than a two-sample difference. |
