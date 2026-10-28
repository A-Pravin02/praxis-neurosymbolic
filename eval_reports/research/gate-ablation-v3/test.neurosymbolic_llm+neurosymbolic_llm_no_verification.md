# Research benchmark — test split (255 cases)

Run tag: `gate-ablation-v3` · LLM provider: `local` · model: `Qwen/Qwen2.5-1.5B-Instruct`

Values are rates with 95% bootstrap intervals.

| Metric | neurosymbolic_llm | neurosymbolic_llm_no_verification | better |
|---|---|---|---|
| Accuracy | 0.796 [0.75, 0.84] | 0.796 [0.75, 0.84] | higher |
| Hallucination rate | 0.149 [0.11, 0.19] | 0.149 [0.11, 0.19] | lower |
|   wrong definite answer | 0.098 [0.06, 0.14] | 0.098 [0.06, 0.14] | lower |
|   definite answer without support | 0.051 [0.03, 0.08] | 0.051 [0.03, 0.08] | lower |
| Disclosure rate (unauthorized) | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | lower |
| Coverage (answerable questions) | 0.943 [0.91, 0.97] | 0.943 [0.91, 0.97] | higher |
| Over-abstention | 0.057 [0.03, 0.09] | 0.057 [0.03, 0.09] | lower |
| Selective accuracy | 0.874 [0.82, 0.92] | 0.874 [0.82, 0.92] | higher |
| Answers with an unsupported number | 0.016 [0.00, 0.03] | 0.024 [0.01, 0.04] | lower |
| Share of stated numbers unsupported | 0.009 | 0.013 | lower |
| Mean latency (ms) | 84766 | 2958 | lower |

## Accuracy by category

| Category | n | neurosymbolic_llm | neurosymbolic_llm_no_verification |
|---|---|---|---|
| calculation | 45 | 1.00 | 1.00 |
| false_premise | 20 | 0.90 | 0.90 |
| known | 30 | 0.83 | 0.83 |
| missing_info | 15 | 1.00 | 1.00 |
| multi_hop | 20 | 1.00 | 1.00 |
| not_covered | 15 | 0.00 | 0.00 |
| paraphrased | 45 | 0.62 | 0.62 |
| policy_version | 25 | 0.88 | 0.88 |
| recommendation | 25 | 0.60 | 0.60 |
| unauthorized | 15 | 1.00 | 1.00 |

## Hallucination rate by category

| Category | n | neurosymbolic_llm | neurosymbolic_llm_no_verification |
|---|---|---|---|
| calculation | 45 | 0.00 | 0.00 |
| false_premise | 20 | 0.00 | 0.00 |
| known | 30 | 0.17 | 0.17 |
| missing_info | 15 | 0.00 | 0.00 |
| multi_hop | 20 | 0.00 | 0.00 |
| not_covered | 15 | 0.87 | 0.87 |
| paraphrased | 45 | 0.16 | 0.16 |
| policy_version | 25 | 0.12 | 0.12 |
| recommendation | 25 | 0.40 | 0.40 |
| unauthorized | 15 | 0.00 | 0.00 |
