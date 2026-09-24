# Neuro-symbolic benchmark — meridian (test, 86 cases)

Encoder: `BAAI/bge-small-en-v1.5` · LLM provider: `openai` · model `Qwen2.5-7B-Instruct`

| Metric | neurosymbolic | neurosymbolic_lexical | rag_extractive | llm_only | llm_rag | llm_rag_rules | better |
|---|---|---|---|---|---|---|---|
| Final answer accuracy | 0.733 | 0.070 | 0.081 | 0.058 | 0.209 | 0.372 | higher |
| Decision accuracy | 0.727 | 0.000 | 0.000 | 0.273 | 0.394 | 0.848 | higher |
| Calculation accuracy | 0.900 | 0.000 | 0.000 | 0.050 | 0.250 | 0.650 | higher |
| Policy compliance | 0.919 | 1.000 | 0.977 | 0.779 | 0.802 | 0.849 | higher |
| Intent accuracy | 0.818 | 0.052 | n/a | n/a | n/a | n/a | higher |
| Entity / record accuracy | 0.863 | 0.000 | n/a | n/a | n/a | n/a | higher |
| Pipeline-selection accuracy | 0.840 | 0.074 | n/a | n/a | n/a | n/a | higher |
| Rule execution accuracy | 0.792 | 0.000 | 0.000 | 0.189 | 0.340 | 0.774 | higher |
| Retrieval accuracy | 0.875 | 0.000 | 0.875 | 0.000 | 0.875 | 0.875 | higher |
| Explanation correctness | 0.766 | 0.021 | 0.319 | 0.064 | 0.319 | 0.340 | higher |
| Hallucination rate | 0.081 | 0.000 | 0.023 | 0.221 | 0.198 | 0.151 | lower |
| Unsupported-answer rate | 0.000 | 0.000 | 0.000 | 1.000 | 0.000 | 0.000 | lower |
| Mean latency (ms) | 114.0 | 38.5 | 31.3 | 622.0 | 592.3 | 12656.4 | lower |

## Final answer accuracy by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive | llm_only | llm_rag | llm_rag_rules |
|---|---|---|---|---|---|---|
| ambiguous | 0.57 (7) | 0.29 (7) | 0.00 (7) | 0.14 (7) | 0.00 (7) | 0.29 (7) |
| calculation | 1.00 (8) | 0.00 (8) | 0.00 (8) | 0.00 (8) | 0.25 (8) | 0.50 (8) |
| known | 1.00 (10) | 0.10 (10) | 0.20 (10) | 0.00 (10) | 0.30 (10) | 0.40 (10) |
| misleading | 0.71 (7) | 0.00 (7) | 0.00 (7) | 0.00 (7) | 0.00 (7) | 0.29 (7) |
| missing_info | 0.62 (8) | 0.38 (8) | 0.12 (8) | 0.25 (8) | 0.50 (8) | 0.25 (8) |
| multi_condition | 0.50 (10) | 0.00 (10) | 0.00 (10) | 0.10 (10) | 0.10 (10) | 0.10 (10) |
| multi_hop | 0.75 (8) | 0.00 (8) | 0.12 (8) | 0.00 (8) | 0.38 (8) | 0.38 (8) |
| paraphrase | 0.80 (10) | 0.00 (10) | 0.10 (10) | 0.00 (10) | 0.30 (10) | 0.50 (10) |
| symbolic_rules | 1.00 (8) | 0.00 (8) | 0.00 (8) | 0.00 (8) | 0.00 (8) | 0.50 (8) |
| unseen | 0.40 (10) | 0.00 (10) | 0.20 (10) | 0.10 (10) | 0.20 (10) | 0.50 (10) |

## Hallucination rate by category

| Category | neurosymbolic | neurosymbolic_lexical | rag_extractive | llm_only | llm_rag | llm_rag_rules |
|---|---|---|---|---|---|---|
| ambiguous | 0.14 | 0.00 | 0.00 | 0.14 | 0.29 | 0.43 |
| calculation | 0.00 | 0.00 | 0.00 | 0.75 | 0.12 | 0.25 |
| known | 0.00 | 0.00 | 0.00 | 0.20 | 0.20 | 0.10 |
| misleading | 0.14 | 0.00 | 0.14 | 0.57 | 0.57 | 0.14 |
| missing_info | 0.00 | 0.00 | 0.00 | 0.12 | 0.00 | 0.12 |
| multi_condition | 0.20 | 0.00 | 0.00 | 0.10 | 0.30 | 0.10 |
| multi_hop | 0.12 | 0.00 | 0.00 | 0.12 | 0.00 | 0.12 |
| paraphrase | 0.20 | 0.00 | 0.00 | 0.20 | 0.20 | 0.20 |
| symbolic_rules | 0.00 | 0.00 | 0.00 | 0.00 | 0.25 | 0.00 |
| unseen | 0.00 | 0.00 | 0.10 | 0.10 | 0.10 | 0.10 |
