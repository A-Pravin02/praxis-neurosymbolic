# Research benchmark — test split (255 cases)

Run tag: `relevance-gate` · LLM provider: `local` · model: `Qwen/Qwen2.5-1.5B-Instruct`

Values are rates with 95% bootstrap intervals.

| Metric | ns_parser | better |
|---|---|---|
| Accuracy | 0.714 [0.66, 0.77] | higher |
| Hallucination rate | 0.094 [0.06, 0.13] | lower |
|   wrong definite answer | 0.078 [0.05, 0.11] | lower |
|   definite answer without support | 0.016 [0.00, 0.03] | lower |
| Disclosure rate (unauthorized) | 0.000 [0.00, 0.00] | lower |
| Coverage (answerable questions) | 0.814 [0.76, 0.87] | higher |
| Over-abstention | 0.186 [0.13, 0.24] | lower |
| Selective accuracy | 0.883 [0.83, 0.93] | higher |
| Answers with an unsupported number | 0.000 [0.00, 0.00] | lower |
| Share of stated numbers unsupported | 0.000 | lower |
| Mean latency (ms) | 1359 | lower |

## Accuracy by category

| Category | n | ns_parser |
|---|---|---|
| calculation | 45 | 0.64 |
| false_premise | 20 | 0.90 |
| known | 30 | 0.83 |
| missing_info | 15 | 0.73 |
| multi_hop | 20 | 0.70 |
| not_covered | 15 | 1.00 |
| paraphrased | 45 | 0.62 |
| policy_version | 25 | 0.88 |
| recommendation | 25 | 0.60 |
| unauthorized | 15 | 0.33 |

## Hallucination rate by category

| Category | n | ns_parser |
|---|---|---|
| calculation | 45 | 0.00 |
| false_premise | 20 | 0.00 |
| known | 30 | 0.00 |
| missing_info | 15 | 0.27 |
| multi_hop | 20 | 0.30 |
| not_covered | 15 | 0.00 |
| paraphrased | 45 | 0.02 |
| policy_version | 25 | 0.12 |
| recommendation | 25 | 0.40 |
| unauthorized | 15 | 0.00 |
