# Router threshold sweep — dev split (21 labelled questions)

Shipped defaults: GOAL_ACCEPT 0.6, RETRIEVAL_ACCEPT 0.55.

Selection is on `benchmark/dev.yaml` only. The test split is not read by this script, so the accuracy reported on it elsewhere is measured on questions no threshold was fitted to.

| GOAL_ACCEPT | RETRIEVAL_ACCEPT | Goal accuracy | Silence rate |
|---|---|---|---|
| 0.50 | 0.45 | 0.905 **(best)** | 0.048 |
| 0.50 | 0.50 | 0.905 | 0.048 |
| 0.50 | 0.55 | 0.905 | 0.048 |
| 0.50 | 0.60 | 0.905 | 0.048 |
| 0.50 | 0.65 | 0.905 | 0.048 |
| 0.55 | 0.45 | 0.905 | 0.048 |
| 0.55 | 0.50 | 0.905 | 0.048 |
| 0.55 | 0.55 | 0.905 | 0.048 |
| 0.55 | 0.60 | 0.905 | 0.048 |
| 0.55 | 0.65 | 0.905 | 0.048 |
| 0.60 | 0.45 | 0.857 | 0.095 |
| 0.60 | 0.50 | 0.857 | 0.095 |
| 0.60 *(shipped)* | 0.55 | 0.857 | 0.095 |
| 0.60 | 0.60 | 0.857 | 0.095 |
| 0.60 | 0.65 | 0.857 | 0.095 |
| 0.65 | 0.45 | 0.857 | 0.095 |
| 0.65 | 0.50 | 0.857 | 0.095 |
| 0.65 | 0.55 | 0.857 | 0.095 |
| 0.65 | 0.60 | 0.857 | 0.095 |
| 0.65 | 0.65 | 0.857 | 0.095 |
| 0.70 | 0.45 | 0.810 | 0.143 |
| 0.70 | 0.50 | 0.810 | 0.143 |
| 0.70 | 0.55 | 0.810 | 0.143 |
| 0.70 | 0.60 | 0.810 | 0.143 |
| 0.70 | 0.65 | 0.810 | 0.143 |

**RETRIEVAL_ACCEPT does not move this metric.** It gates the path taken when no goal is reached, so goal accuracy cannot see it. This sweep therefore justifies GOAL_ACCEPT only; RETRIEVAL_ACCEPT needs its own objective over informational questions, and is still hand-set.

With 21 dev questions one question is 0.048, so differences of that size are a single case, not a trend.

Best on dev: GOAL_ACCEPT 0.50, RETRIEVAL_ACCEPT 0.45 (goal accuracy 0.905, silence 0.048).

Shipped point scores 0.857 with 0.095 silence.
The swept best differs from what ships; changing the default is a separate decision, taken deliberately and re-measured on test.