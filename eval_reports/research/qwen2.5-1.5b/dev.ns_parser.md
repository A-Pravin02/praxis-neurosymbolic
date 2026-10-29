# Research benchmark — dev split (153 cases)

Run tag: `qwen2.5-1.5b` · LLM provider: `openai` · model: `Qwen2.5-1.5B-Instruct`

Values are rates with 95% bootstrap intervals.

| Metric | ns_parser | better |
|---|---|---|
| Accuracy | 0.804 [0.74, 0.86] | higher |
| Hallucination rate | 0.046 [0.01, 0.09] | lower |
|   wrong definite answer | 0.046 [0.01, 0.09] | lower |
|   definite answer without support | 0.000 [0.00, 0.00] | lower |
| Disclosure rate (unauthorized) | 0.000 [0.00, 0.00] | lower |
| Coverage (answerable questions) | 0.881 [0.82, 0.94] | higher |
| Over-abstention | 0.119 [0.06, 0.18] | lower |
| Selective accuracy | 0.937 [0.89, 0.97] | higher |
| Answers with an unsupported number | 0.020 [0.00, 0.05] | lower |
| Share of stated numbers unsupported | 0.017 | lower |
| Mean latency (ms) | 7209 | lower |

## Accuracy by category

| Category | n | ns_parser |
|---|---|---|
| calculation | 27 | 0.89 |
| false_premise | 12 | 0.42 |
| known | 18 | 0.83 |
| missing_info | 9 | 1.00 |
| multi_hop | 12 | 0.67 |
| not_covered | 9 | 1.00 |
| paraphrased | 27 | 0.93 |
| policy_version | 15 | 0.87 |
| recommendation | 15 | 0.93 |
| unauthorized | 9 | 0.11 |

## Hallucination rate by category

| Category | n | ns_parser |
|---|---|---|
| calculation | 27 | 0.00 |
| false_premise | 12 | 0.00 |
| known | 18 | 0.00 |
| missing_info | 9 | 0.00 |
| multi_hop | 12 | 0.33 |
| not_covered | 9 | 0.00 |
| paraphrased | 27 | 0.00 |
| policy_version | 15 | 0.13 |
| recommendation | 15 | 0.07 |
| unauthorized | 9 | 0.00 |
