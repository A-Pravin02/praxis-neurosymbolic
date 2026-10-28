# Statistical validation

Generated 2026-10-28 09:59 UTC by `scripts/make_audit_reports.py`, recomputing each test from the per-question predictions rather than reading the stored value.

**Test.** Exact two-sided McNemar on paired per-question correctness. Concordant pairs carry no information, so of the discordant pairs only, the count favouring one system is tested against Binomial(n, 1/2). The exact binomial tail is used rather than the chi-square approximation, because the discordant counts are small.

| Split | Comparison | n | favours A | favours B | p (recomputed) | p (stored) | agree |
|---|---|---:|---:|---:|---|---|---|
| test | neurosymbolic vs rag_extractive | 255 | 203 | 0 | 1.56e-61 | 1.56e-61 | yes |
| test | neurosymbolic vs llm_only | 255 | 152 | 9 | 1.16e-34 | 1.16e-34 | yes |
| test | neurosymbolic vs llm_rag | 255 | 168 | 16 | 3.8e-33 | 3.8e-33 | yes |
| test | neurosymbolic vs llm_rag_selfcheck | 255 | 162 | 22 | 1.52e-27 | 1.52e-27 | yes |
| test | neurosymbolic vs llm_rag_rules | 255 | 99 | 33 | 7.5e-09 | 7.5e-09 | yes |
| test | neurosymbolic vs ns_parser | 255 | 2 | 40 | 4.11e-10 | 4.11e-10 | yes |
| test | neurosymbolic vs ns_no_verification | 255 | 0 | 0 | 1 | 1 | yes |
| test | neurosymbolic vs ns_two_valued | 255 | 15 | 2 | 0.00235 | 0.00235 | yes |
| test | neurosymbolic vs ns_latest_version | 255 | 9 | 0 | 0.00391 | 0.00391 | yes |
| test | neurosymbolic vs ns_trust_statements | 255 | 27 | 0 | 1.49e-08 | 1.49e-08 | yes |
| test | neurosymbolic vs ns_no_authorization | 255 | 15 | 0 | 6.1e-05 | 6.1e-05 | yes |
| test | neurosymbolic vs ns_no_number_check | 255 | 0 | 0 | 1 | 1 | yes |
| test | neurosymbolic vs ns_no_kind_agreement | 255 | 1 | 3 | 0.625 | 0.625 | yes |
| confirm | neurosymbolic vs ns_parser | 255 | 1 | 7 | 0.0703 | 0.0703 | yes |
| confirm | neurosymbolic vs ns_no_authorization | 255 | 14 | 0 | 0.000122 | 0.000122 | yes |
| confirm | neurosymbolic vs llm_rag | 255 | 168 | 16 | 3.8e-33 | 3.8e-33 | yes |
| confirm | neurosymbolic vs llm_only | 255 | 156 | 10 | 7.58e-35 | 7.58e-35 | yes |

**17 comparisons recomputed, 0 disagreed with the stored value.**

## The two tests quoted in the paper

Both were additionally checked by hand against the closed form:

- Confirmatory split, 7 against 1: n = 8, k = 1, p = 2(C(8,0)+C(8,1))/2^8 = 18/256 = 0.0703.
- Test split, 40 against 2: n = 42, k = 2, p = 2(1+42+861)/2^42 = 1808/2^42 = 4.11e-10.

At the 0.05 level the test split is persuasive and the confirmatory split is not. The paper states this distinction and does not claim that the parser's advantage generalizes.

## Appropriateness

Every system answers the same questions, so the comparisons are paired and an unpaired test would discard that structure and lose power. McNemar is the standard paired test for two classifiers on one sample. `scipy` is not installed in this environment, so no third-party implementation was used as a cross-check; the closed-form arithmetic above serves instead.
