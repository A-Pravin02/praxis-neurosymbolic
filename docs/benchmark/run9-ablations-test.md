# Research benchmark — test split (255 cases)

Run tag: `ablations-test` · LLM provider: `mock` · model: `n/a`

Values are rates with 95% bootstrap intervals.

| Metric | neurosymbolic | ns_two_valued | ns_no_verification | ns_latest_version | ns_trust_statements | ns_no_authorization | neurosymbolic_lexical | better |
|---|---|---|---|---|---|---|---|---|
| Accuracy | 0.796 [0.75, 0.84] | 0.745 [0.69, 0.80] | 0.796 [0.75, 0.84] | 0.761 [0.71, 0.81] | 0.690 [0.64, 0.75] | 0.737 [0.68, 0.79] | 0.059 [0.03, 0.09] | higher |
| Hallucination rate | 0.149 [0.11, 0.19] | 0.188 [0.14, 0.24] | 0.149 [0.11, 0.19] | 0.184 [0.14, 0.23] | 0.094 [0.06, 0.13] | 0.208 [0.16, 0.26] | 0.000 [0.00, 0.00] | lower |
|   wrong definite answer | 0.098 [0.06, 0.14] | 0.098 [0.06, 0.14] | 0.098 [0.06, 0.14] | 0.133 [0.09, 0.18] | 0.043 [0.02, 0.07] | 0.098 [0.06, 0.14] | 0.000 [0.00, 0.00] | lower |
|   definite answer without support | 0.051 [0.03, 0.08] | 0.090 [0.06, 0.13] | 0.051 [0.03, 0.08] | 0.051 [0.03, 0.08] | 0.051 [0.03, 0.08] | 0.110 [0.07, 0.15] | 0.000 [0.00, 0.00] | lower |
| Disclosure rate (unauthorized) | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.000 [0.00, 0.00] | 0.867 [0.67, 1.00] | 0.000 [0.00, 0.00] | lower |
| Coverage (answerable questions) | 0.943 [0.91, 0.97] | 0.952 [0.92, 0.98] | 0.943 [0.91, 0.97] | 0.943 [0.91, 0.97] | 0.748 [0.69, 0.80] | 0.943 [0.91, 0.97] | 0.000 [0.00, 0.00] | higher |
| Over-abstention | 0.057 [0.03, 0.09] | 0.048 [0.02, 0.08] | 0.057 [0.03, 0.09] | 0.057 [0.03, 0.09] | 0.252 [0.20, 0.31] | 0.057 [0.03, 0.09] | 1.000 [1.00, 1.00] | lower |
| Selective accuracy | 0.874 [0.82, 0.92] | 0.875 [0.82, 0.92] | 0.874 [0.82, 0.92] | 0.828 [0.77, 0.88] | 0.930 [0.89, 0.97] | 0.874 [0.82, 0.92] | n/a | higher |
| Answers with an unsupported number | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | 0.012 [0.00, 0.03] | 0.000 [0.00, 0.00] | lower |
| Share of stated numbers unsupported | 0.007 | 0.007 | 0.007 | 0.007 | 0.008 | 0.007 | n/a | lower |
| Mean latency (ms) | 56 | 3 | 3 | 3 | 3 | 4 | 14 | lower |

## Accuracy by category

| Category | n | neurosymbolic | ns_two_valued | ns_no_verification | ns_latest_version | ns_trust_statements | ns_no_authorization | neurosymbolic_lexical |
|---|---|---|---|---|---|---|---|---|
| calculation | 45 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 |
| false_premise | 20 | 0.90 | 1.00 | 0.90 | 0.90 | 0.00 | 0.90 | 0.00 |
| known | 30 | 0.83 | 0.83 | 0.83 | 0.83 | 0.83 | 0.83 | 0.00 |
| missing_info | 15 | 1.00 | 0.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 |
| multi_hop | 20 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 |
| not_covered | 15 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 |
| paraphrased | 45 | 0.62 | 0.62 | 0.62 | 0.60 | 0.42 | 0.62 | 0.00 |
| policy_version | 25 | 0.88 | 0.88 | 0.88 | 0.64 | 0.88 | 0.88 | 0.00 |
| recommendation | 25 | 0.60 | 0.60 | 0.60 | 0.52 | 0.60 | 0.60 | 0.00 |
| unauthorized | 15 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.00 |

## Hallucination rate by category

| Category | n | neurosymbolic | ns_two_valued | ns_no_verification | ns_latest_version | ns_trust_statements | ns_no_authorization | neurosymbolic_lexical |
|---|---|---|---|---|---|---|---|---|
| calculation | 45 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| false_premise | 20 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| known | 30 | 0.17 | 0.17 | 0.17 | 0.17 | 0.17 | 0.17 | 0.00 |
| missing_info | 15 | 0.00 | 0.67 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| multi_hop | 20 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| not_covered | 15 | 0.87 | 0.87 | 0.87 | 0.87 | 0.87 | 0.87 | 0.00 |
| paraphrased | 45 | 0.16 | 0.16 | 0.16 | 0.18 | 0.07 | 0.16 | 0.00 |
| policy_version | 25 | 0.12 | 0.12 | 0.12 | 0.36 | 0.12 | 0.12 | 0.00 |
| recommendation | 25 | 0.40 | 0.40 | 0.40 | 0.48 | 0.00 | 0.40 | 0.00 |
| unauthorized | 15 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 1.00 | 0.00 |

## Paired comparison with `neurosymbolic` (exact McNemar)

| System | correct only NS | correct only system | p | hallucinated only NS | hallucinated only system | p |
|---|---|---|---|---|---|---|
| ns_two_valued | 15 | 2 | 0.0023 | 0 | 10 | 0.002 |
| ns_no_verification | 0 | 0 | 1 | 0 | 0 | 1 |
| ns_latest_version | 9 | 0 | 0.0039 | 0 | 9 | 0.0039 |
| ns_trust_statements | 27 | 0 | 1.5e-08 | 14 | 0 | 0.00012 |
| ns_no_authorization | 15 | 0 | 6.1e-05 | 0 | 15 | 6.1e-05 |
| neurosymbolic_lexical | 203 | 15 | 2.9e-43 | 38 | 0 | 7.3e-12 |
