# Unresolved items

Generated 2026-10-28 09:59 UTC. Each entry states what is missing and what would settle it.

| Item | Status | What it would take |
|---|---|---|
| Verifier precision and recall | Unmeasured | The verifier is correct by construction with respect to its own graph. Independent ground-truth labels, authored without reference to the rule base, would be needed. |
| Question relevance | Not implemented | A relevance gate between the resolved goal and the question. Until then the system answers out-of-scope questions confidently (0 of 15 abstentions). |
| Model capability effect | Open question | The 1.5B and 7B runs used different inference stacks. One stack serving both sizes on the same split. |
| Independently authored evaluation | Not done | A second person writing questions and gold answers without seeing the rule base. |
| Conference reference pagination | Partly verified | Pages and DOIs confirmed for 5 journal and 5 conference entries. Self-RAG, Program-of-Thoughts and Binder publish through OpenReview without page numbers; the NeurIPS entry's volume is confirmed but its page range is not. |
| Prose-faithfulness metric | Not implemented | Needed to quantify the gate's contribution beyond counting changed answers and unsupported figures. |
| Run-to-run variance | Estimated from two runs | Three to five seeded repeats would give a variance estimate rather than a two-sample difference. |
