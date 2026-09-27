# Neuro-symbolic benchmark — harbour (test, 40 cases)

Encoder: `BAAI/bge-small-en-v1.5` · LLM provider: `openai`

| Metric | neurosymbolic | neurosymbolic_lexical | rag_extractive | better |
|---|---|---|---|---|
| Final answer accuracy | 0.850 | 0.125 | 0.125 | higher |
| Decision accuracy | 0.818 | 0.000 | 0.000 | higher |
| Calculation accuracy | 0.909 | 0.000 | 0.000 | higher |
| Policy compliance | 1.000 | 1.000 | 0.950 | higher |
| Intent accuracy | 0.889 | 0.194 | n/a | higher |
| Entity / record accuracy | 0.931 | 0.000 | n/a | higher |
| Pipeline-selection accuracy | 0.875 | 0.150 | n/a | higher |
| Rule execution accuracy | 0.864 | 0.000 | 0.000 | higher |
| Retrieval accuracy | 0.875 | 0.000 | 0.375 | higher |
| Explanation correctness | 0.900 | 0.200 | 0.900 | higher |
| Hallucination rate | 0.000 | 0.000 | 0.050 | lower |
| Unsupported-answer rate | 0.000 | 0.000 | 0.000 | lower |
| Mean latency (ms) | 71.5 | 24.3 | 30.6 | lower |

## Final answer accuracy by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| calculation | 1.00 (5) | 0.00 (5) | 0.00 (5) |
| known | 1.00 (8) | 0.12 (8) | 0.12 (8) |
| misleading | 0.67 (3) | 0.00 (3) | 0.00 (3) |
| missing_info | 1.00 (4) | 0.00 (4) | 0.00 (4) |
| multi_condition | 0.67 (3) | 0.00 (3) | 0.00 (3) |
| multi_hop | 0.67 (3) | 0.33 (3) | 0.67 (3) |
| paraphrase | 0.50 (6) | 0.00 (6) | 0.17 (6) |
| symbolic_rules | 1.00 (3) | 0.00 (3) | 0.00 (3) |
| unauthorized | 1.00 (3) | 0.33 (3) | 0.00 (3) |
| unseen | 1.00 (2) | 1.00 (2) | 0.50 (2) |

## Hallucination rate by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive |
|---|---|---|---|
| calculation | 0.00 | 0.00 | 0.00 |
| known | 0.00 | 0.00 | 0.12 |
| misleading | 0.00 | 0.00 | 0.00 |
| missing_info | 0.00 | 0.00 | 0.00 |
| multi_condition | 0.00 | 0.00 | 0.00 |
| multi_hop | 0.00 | 0.00 | 0.00 |
| paraphrase | 0.00 | 0.00 | 0.00 |
| symbolic_rules | 0.00 | 0.00 | 0.00 |
| unauthorized | 0.00 | 0.00 | 0.33 |
| unseen | 0.00 | 0.00 | 0.00 |
