# Research benchmark — confirm split (255 cases)

Run tag: `confirm` · LLM provider: `openai` · model: `Qwen2.5-7B-Instruct`

Values are rates with 95% bootstrap intervals.

| Metric | neurosymbolic | ns_parser | ns_no_authorization | llm_rag | llm_only | better |
|---|---|---|---|---|---|---|
| Accuracy | 0.812 [0.76, 0.86] | 0.835 [0.79, 0.88] | 0.757 [0.71, 0.81] | 0.216 [0.16, 0.27] | 0.239 [0.19, 0.29] | higher |
| Hallucination rate | 0.133 [0.09, 0.18] | 0.118 [0.08, 0.16] | 0.188 [0.14, 0.24] | 0.306 [0.25, 0.36] | 0.455 [0.40, 0.51] | lower |
|   wrong definite answer | 0.086 [0.05, 0.12] | 0.071 [0.04, 0.10] | 0.086 [0.05, 0.12] | 0.224 [0.17, 0.27] | 0.372 [0.31, 0.43] | lower |
|   definite answer without support | 0.047 [0.02, 0.07] | 0.047 [0.02, 0.07] | 0.102 [0.07, 0.14] | 0.082 [0.05, 0.12] | 0.082 [0.05, 0.12] | lower |
| Disclosure rate (unauthorized) | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.867 [0.67, 1.00] | 0.067 [0.00, 0.20] | 0.000 [0.00, 0.00] | lower |
| Coverage (answerable questions) | 0.943 [0.91, 0.97] | 0.948 [0.91, 0.98] | 0.943 [0.91, 0.97] | 0.481 [0.41, 0.55] | 0.652 [0.59, 0.72] | higher |
| Over-abstention | 0.057 [0.03, 0.09] | 0.052 [0.02, 0.09] | 0.057 [0.03, 0.09] | 0.519 [0.45, 0.59] | 0.348 [0.28, 0.41] | lower |
| Selective accuracy | 0.889 [0.84, 0.93] | 0.909 [0.86, 0.95] | 0.889 [0.84, 0.93] | 0.436 [0.34, 0.53] | 0.307 [0.23, 0.38] | higher |
| Answers with an unsupported number | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | 0.039 [0.02, 0.06] | 0.161 [0.12, 0.20] | lower |
| Share of stated numbers unsupported | 0.007 | 0.007 | 0.007 | 0.068 | 0.333 | lower |
| Mean latency (ms) | 90 | 1881 | 79 | 5966 | 5416 | lower |

## Accuracy by category

| Category | n | neurosymbolic | ns_parser | ns_no_authorization | llm_rag | llm_only |
|---|---|---|---|---|---|---|
| calculation | 45 | 1.00 | 1.00 | 1.00 | 0.13 | 0.07 |
| false_premise | 20 | 0.90 | 0.90 | 0.90 | 0.00 | 0.00 |
| known | 30 | 0.97 | 0.93 | 0.97 | 0.00 | 0.33 |
| missing_info | 15 | 1.00 | 1.00 | 1.00 | 0.07 | 0.67 |
| multi_hop | 20 | 0.95 | 1.00 | 0.95 | 0.90 | 0.20 |
| not_covered | 15 | 0.13 | 0.20 | 0.13 | 0.67 | 0.60 |
| paraphrased | 45 | 0.60 | 0.71 | 0.60 | 0.18 | 0.13 |
| policy_version | 25 | 0.92 | 0.92 | 0.92 | 0.44 | 0.56 |
| recommendation | 25 | 0.60 | 0.60 | 0.60 | 0.04 | 0.20 |
| unauthorized | 15 | 0.93 | 0.93 | 0.00 | 0.00 | 0.00 |

## Hallucination rate by category

| Category | n | neurosymbolic | ns_parser | ns_no_authorization | llm_rag | llm_only |
|---|---|---|---|---|---|---|
| calculation | 45 | 0.00 | 0.00 | 0.00 | 0.13 | 0.64 |
| false_premise | 20 | 0.00 | 0.00 | 0.00 | 0.85 | 1.00 |
| known | 30 | 0.03 | 0.03 | 0.03 | 0.17 | 0.17 |
| missing_info | 15 | 0.00 | 0.00 | 0.00 | 0.27 | 0.13 |
| multi_hop | 20 | 0.05 | 0.00 | 0.05 | 0.10 | 0.05 |
| not_covered | 15 | 0.80 | 0.80 | 0.80 | 0.33 | 0.40 |
| paraphrased | 45 | 0.18 | 0.11 | 0.18 | 0.29 | 0.36 |
| policy_version | 25 | 0.08 | 0.08 | 0.08 | 0.28 | 0.40 |
| recommendation | 25 | 0.40 | 0.40 | 0.40 | 0.28 | 0.56 |
| unauthorized | 15 | 0.00 | 0.00 | 0.93 | 0.80 | 0.87 |

## Paired comparison with `neurosymbolic` (exact McNemar)

| System | correct only NS | correct only system | p | hallucinated only NS | hallucinated only system | p |
|---|---|---|---|---|---|---|
| ns_parser | 1 | 7 | 0.07 | 4 | 0 | 0.12 |
| ns_no_authorization | 14 | 0 | 0.00012 | 0 | 14 | 0.00012 |
| llm_rag | 168 | 16 | 3.8e-33 | 27 | 71 | 1e-05 |
| llm_only | 156 | 10 | 7.6e-35 | 24 | 106 | 1.7e-13 |
