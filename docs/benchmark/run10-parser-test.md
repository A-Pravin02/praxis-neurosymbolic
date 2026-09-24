# Research benchmark — test split (255 cases)

Run tag: `parser-test` · LLM provider: `openai` · model: `Qwen2.5-7B-Instruct`

Values are rates with 95% bootstrap intervals.

| Metric | ns_parser | better |
|---|---|---|
| Accuracy | 0.906 [0.87, 0.94] | higher |
| Hallucination rate | 0.035 [0.02, 0.06] | lower |
|   wrong definite answer | 0.000 [0.00, 0.00] | lower |
|   definite answer without support | 0.035 [0.02, 0.06] | lower |
| Disclosure rate (unauthorized) | 0.000 [0.00, 0.00] | lower |
| Coverage (answerable questions) | 0.929 [0.90, 0.96] | higher |
| Over-abstention | 0.071 [0.04, 0.10] | lower |
| Selective accuracy | 1.000 [1.00, 1.00] | higher |
| Answers with an unsupported number | 0.012 [0.00, 0.03] | lower |
| Share of stated numbers unsupported | 0.007 | lower |
| Mean latency (ms) | 14778 | lower |

## Accuracy by category

| Category | n | ns_parser |
|---|---|---|
| calculation | 45 | 0.78 |
| false_premise | 20 | 1.00 |
| known | 30 | 0.83 |
| missing_info | 15 | 1.00 |
| multi_hop | 20 | 1.00 |
| not_covered | 15 | 0.40 |
| paraphrased | 45 | 1.00 |
| policy_version | 25 | 1.00 |
| recommendation | 25 | 1.00 |
| unauthorized | 15 | 1.00 |

## Hallucination rate by category

| Category | n | ns_parser |
|---|---|---|
| calculation | 45 | 0.00 |
| false_premise | 20 | 0.00 |
| known | 30 | 0.00 |
| missing_info | 15 | 0.00 |
| multi_hop | 20 | 0.00 |
| not_covered | 15 | 0.60 |
| paraphrased | 45 | 0.00 |
| policy_version | 25 | 0.00 |
| recommendation | 25 | 0.00 |
| unauthorized | 15 | 0.00 |
