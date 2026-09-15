# Neuro-symbolic benchmark — meridian (copilot, 53 cases)

Encoder: `BAAI/bge-small-en-v1.5` · LLM provider: `mock`

| Metric | neurosymbolic | neurosymbolic_lexical | rag_extractive | better |
|---|---|---|---|---|
| Final answer accuracy | 0.774 | 0.038 | 0.038 | higher |
| Decision accuracy | 0.840 | 0.000 | 0.000 | higher |
| Calculation accuracy | 0.833 | 0.000 | 0.000 | higher |
| Policy compliance | 0.925 | 1.000 | 0.962 | higher |
| Intent accuracy | 0.830 | 0.000 | n/a | higher |
| Entity / record accuracy | 0.967 | 0.000 | n/a | higher |
| Pipeline-selection accuracy | 1.000 | 0.000 | n/a | higher |
| Rule execution accuracy | 0.838 | 0.000 | 0.000 | higher |
| Retrieval accuracy | n/a | n/a | n/a | higher |
| Explanation correctness | 0.750 | 0.000 | 0.600 | higher |
| Hallucination rate | 0.075 | 0.000 | 0.038 | lower |
| Unsupported-answer rate | 0.000 | n/a | 0.000 | lower |
| Mean latency (ms) | 113.6 | 30.5 | 47.4 | lower |

`llm_only` not run: no real language model is configured (LLM_PROVIDER=mock); not simulated.

`llm_rag` not run: no real language model is configured (LLM_PROVIDER=mock); not simulated.

`llm_rag_rules` not run: no real language model is configured (LLM_PROVIDER=mock); not simulated.

## Final answer accuracy by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| adversarial | 1.00 (9) | 0.00 (9) | 0.11 (9) |
| calculation | 0.80 (5) | 0.00 (5) | 0.00 (5) |
| conflicting_policy | 0.75 (4) | 0.00 (4) | 0.00 (4) |
| known | 1.00 (6) | 0.00 (6) | 0.00 (6) |
| missing_info | 0.67 (6) | 0.33 (6) | 0.00 (6) |
| multi_hop | 0.75 (4) | 0.00 (4) | 0.00 (4) |
| paraphrased | 0.33 (6) | 0.00 (6) | 0.17 (6) |
| policy | 0.67 (6) | 0.00 (6) | 0.00 (6) |
| unseen | 0.86 (7) | 0.00 (7) | 0.00 (7) |

## Hallucination rate by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| adversarial | 0.00 | 0.00 | 0.00 |
| calculation | 0.00 | 0.00 | 0.00 |
| conflicting_policy | 0.00 | 0.00 | 0.25 |
| known | 0.00 | 0.00 | 0.00 |
| missing_info | 0.17 | 0.00 | 0.17 |
| multi_hop | 0.00 | 0.00 | 0.00 |
| paraphrased | 0.33 | 0.00 | 0.00 |
| policy | 0.17 | 0.00 | 0.00 |
| unseen | 0.00 | 0.00 | 0.00 |
