# Neuro-symbolic benchmark — meridian (test, 86 cases)

Encoder: `BAAI/bge-small-en-v1.5` · LLM provider: `mock`

| Metric | neurosymbolic | neurosymbolic_lexical | rag_extractive | better |
|---|---|---|---|---|
| Final answer accuracy | 0.779 | 0.093 | 0.105 | higher |
| Intent accuracy | 0.818 | 0.052 | n/a | higher |
| Entity / record accuracy | 0.863 | 0.000 | n/a | higher |
| Pipeline-selection accuracy | 0.864 | 0.099 | n/a | higher |
| Rule execution accuracy | 0.792 | 0.000 | 0.000 | higher |
| Retrieval accuracy | 0.750 | 0.000 | 0.875 | higher |
| Explanation correctness | 0.766 | 0.021 | 0.383 | higher |
| Hallucination rate | 0.081 | 0.000 | 0.012 | lower |
| Unsupported-answer rate | 0.000 | 0.000 | 0.000 | lower |
| Mean latency (ms) | 52.8 | 10.5 | 6.4 | lower |

`llm_rag` not run: no real language model is configured (LLM_PROVIDER=mock); not simulated.

## Final answer accuracy by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| ambiguous | 0.86 (7) | 0.57 (7) | 0.00 (7) |
| calculation | 1.00 (8) | 0.00 (8) | 0.00 (8) |
| known | 1.00 (10) | 0.10 (10) | 0.30 (10) |
| misleading | 0.71 (7) | 0.00 (7) | 0.00 (7) |
| missing_info | 0.75 (8) | 0.38 (8) | 0.12 (8) |
| multi_condition | 0.60 (10) | 0.00 (10) | 0.00 (10) |
| multi_hop | 0.62 (8) | 0.00 (8) | 0.12 (8) |
| paraphrase | 0.90 (10) | 0.00 (10) | 0.10 (10) |
| symbolic_rules | 1.00 (8) | 0.00 (8) | 0.00 (8) |
| unseen | 0.40 (10) | 0.00 (10) | 0.30 (10) |

## Hallucination rate by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| ambiguous | 0.00 | 0.00 | 0.00 |
| calculation | 0.00 | 0.00 | 0.00 |
| known | 0.00 | 0.00 | 0.00 |
| misleading | 0.14 | 0.00 | 0.14 |
| missing_info | 0.00 | 0.00 | 0.00 |
| multi_condition | 0.20 | 0.00 | 0.00 |
| multi_hop | 0.25 | 0.00 | 0.00 |
| paraphrase | 0.10 | 0.00 | 0.00 |
| symbolic_rules | 0.00 | 0.00 | 0.00 |
| unseen | 0.10 | 0.00 | 0.00 |
