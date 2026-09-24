# Calibration — neurosymbolic (test split, 86 scored cases)

Accuracy 0.733 · mean confidence 0.779 · **ECE 0.192** · MCE 0.650 · Brier 0.203

Mean confidence is above accuracy by 0.047 — the system is overconfident on average.

| Confidence bin | Cases | Mean confidence | Accuracy | Gap |
|---|---|---|---|---|
| 0.0–0.1 | 11 | 0.000 | 0.364 | +0.364 |
| 0.1–0.2 | 0 | — | — | — |
| 0.2–0.3 | 0 | — | — | — |
| 0.3–0.4 | 0 | — | — | — |
| 0.4–0.5 | 0 | — | — | — |
| 0.5–0.6 | 1 | 0.530 | 1.000 | +0.470 |
| 0.6–0.7 | 1 | 0.650 | 0.000 | -0.650 |
| 0.7–0.8 | 7 | 0.750 | 1.000 | +0.250 |
| 0.8–0.9 | 24 | 0.847 | 0.750 | -0.097 |
| 0.9–1.0 | 42 | 0.959 | 0.786 | -0.173 |

A well-calibrated system has gaps near zero across bins that hold cases.
Confidence is computed in code and is never read by any scoring metric.