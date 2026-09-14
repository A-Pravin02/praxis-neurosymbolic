# Neuro-symbolic benchmark — meridian (holdout, 40 cases)

Encoder: `BAAI/bge-small-en-v1.5` · LLM provider: `mock`

| Metric | neurosymbolic | neurosymbolic_lexical | rag_extractive | better |
|---|---|---|---|---|
| Final answer accuracy | 0.800 | 0.125 | 0.150 | higher |
| Intent accuracy | 0.844 | 0.062 | n/a | higher |
| Entity / record accuracy | 0.941 | 0.000 | n/a | higher |
| Pipeline-selection accuracy | 0.850 | 0.250 | n/a | higher |
| Rule execution accuracy | 0.840 | 0.000 | 0.000 | higher |
| Retrieval accuracy | 0.667 | 0.000 | 1.000 | higher |
| Explanation correctness | 0.786 | 0.000 | 0.643 | higher |
| Hallucination rate | 0.025 | 0.000 | 0.000 | lower |
| Unsupported-answer rate | 0.000 | n/a | 0.000 | lower |
| Mean latency (ms) | 72.6 | 11.0 | 12.8 | lower |

`llm_rag` not run: no real language model is configured (LLM_PROVIDER=mock); not simulated.

## Final answer accuracy by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| ambiguous | 1.00 (4) | 0.50 (4) | 0.00 (4) |
| calculation | 1.00 (4) | 0.00 (4) | 0.00 (4) |
| known | 0.50 (4) | 0.00 (4) | 0.75 (4) |
| misleading | 1.00 (3) | 0.00 (3) | 0.00 (3) |
| missing_info | 0.50 (4) | 0.50 (4) | 0.00 (4) |
| multi_condition | 0.75 (4) | 0.00 (4) | 0.00 (4) |
| multi_hop | 1.00 (4) | 0.00 (4) | 0.00 (4) |
| paraphrase | 1.00 (4) | 0.00 (4) | 0.25 (4) |
| symbolic_rules | 0.75 (4) | 0.25 (4) | 0.25 (4) |
| unseen | 0.60 (5) | 0.00 (5) | 0.20 (5) |

## Hallucination rate by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| ambiguous | 0.00 | 0.00 | 0.00 |
| calculation | 0.00 | 0.00 | 0.00 |
| known | 0.00 | 0.00 | 0.00 |
| misleading | 0.00 | 0.00 | 0.00 |
| missing_info | 0.25 | 0.00 | 0.00 |
| multi_condition | 0.00 | 0.00 | 0.00 |
| multi_hop | 0.00 | 0.00 | 0.00 |
| paraphrase | 0.00 | 0.00 | 0.00 |
| symbolic_rules | 0.00 | 0.00 | 0.00 |
| unseen | 0.00 | 0.00 | 0.00 |
