# Research benchmark — confirm split (255 cases)

Run tag: `confirm` · LLM provider: `mock` · model: `n/a`

Values are rates with 95% bootstrap intervals.

| Metric | neurosymbolic | ns_no_authorization | better |
|---|---|---|---|
| Accuracy | 0.812 [0.76, 0.86] | 0.757 [0.71, 0.81] | higher |
| Hallucination rate | 0.133 [0.09, 0.18] | 0.188 [0.14, 0.24] | lower |
|   wrong definite answer | 0.086 [0.05, 0.12] | 0.086 [0.05, 0.12] | lower |
|   definite answer without support | 0.047 [0.02, 0.07] | 0.102 [0.07, 0.14] | lower |
| Disclosure rate (unauthorized) | 0.000 [0.00, 0.00] | 0.867 [0.67, 1.00] | lower |
| Coverage (answerable questions) | 0.943 [0.91, 0.97] | 0.943 [0.91, 0.97] | higher |
| Over-abstention | 0.057 [0.03, 0.09] | 0.057 [0.03, 0.09] | lower |
| Selective accuracy | 0.889 [0.84, 0.93] | 0.889 [0.84, 0.93] | higher |
| Answers with an unsupported number | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | lower |
| Share of stated numbers unsupported | 0.007 | 0.007 | lower |
| Mean latency (ms) | 90 | 79 | lower |

## Accuracy by category

| Category | n | neurosymbolic | ns_no_authorization |
|---|---|---|---|
| calculation | 45 | 1.00 | 1.00 |
| false_premise | 20 | 0.90 | 0.90 |
| known | 30 | 0.97 | 0.97 |
| missing_info | 15 | 1.00 | 1.00 |
| multi_hop | 20 | 0.95 | 0.95 |
| not_covered | 15 | 0.13 | 0.13 |
| paraphrased | 45 | 0.60 | 0.60 |
| policy_version | 25 | 0.92 | 0.92 |
| recommendation | 25 | 0.60 | 0.60 |
| unauthorized | 15 | 0.93 | 0.00 |

## Hallucination rate by category

| Category | n | neurosymbolic | ns_no_authorization |
|---|---|---|---|
| calculation | 45 | 0.00 | 0.00 |
| false_premise | 20 | 0.00 | 0.00 |
| known | 30 | 0.03 | 0.03 |
| missing_info | 15 | 0.00 | 0.00 |
| multi_hop | 20 | 0.05 | 0.05 |
| not_covered | 15 | 0.80 | 0.80 |
| paraphrased | 45 | 0.18 | 0.18 |
| policy_version | 25 | 0.08 | 0.08 |
| recommendation | 25 | 0.40 | 0.40 |
| unauthorized | 15 | 0.00 | 0.93 |

## Paired comparison with `neurosymbolic` (exact McNemar)

| System | correct only NS | correct only system | p | hallucinated only NS | hallucinated only system | p |
|---|---|---|---|---|---|---|
| ns_no_authorization | 14 | 0 | 0.00012 | 0 | 14 | 0.00012 |
