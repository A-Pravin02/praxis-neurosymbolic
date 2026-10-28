# Verification-gate evidence

Generated 2026-10-28 09:59 UTC. Each claim in the paper's Section VIII-A is listed with the artifact that supports it and the value recomputed here.

## 1. The switch reaches every guard

`no_verification` must remove the mechanism everywhere it acts. Occurrences:

| File | Reads |
|---|---:|
| `app/hybrid/controller.py` | 2 |
| `app/neural/response_generation.py` | 1 |

Total 3 across 2 files. The three layers are the realizer's unsupported-figure check, the controller's re-verification of what the realizer released, and the release decision on a failed report. Two earlier runs of this experiment produced byte-identical arms because the switch reached only some of them.

## 2. Regression tests

`backend/tests/test_ablation_reach.py` run at report time: **9 passed in 0.20s**

They assert both directions - the guard is on by default and off under the switch - and, structurally, that the switch is read in at least three places. A test checking only the default would have passed throughout the period the defect existed.

## 3. The paired experiment

| Property | Value |
|---|---|
| Cases in both arms | 255 |
| Identical case ids | True |
| Identical categories per case | True |
| Released answer differs | **52** |
| Answers with an unsupported figure | **4 gated, 6 ablated** |

Decision-level fields, differences between arms:

| Field | Differs in |
|---|---:|
| `correct` | 0 |
| `hallucinated` | 0 |
| `definite` | 0 |
| `abstained` | 0 |
| `outcome` | 0 |
| `decision` | 0 |
| `value` | 0 |
| `goal` | 0 |

The two systems are built by the same factory with the same model, retrieval and prompts; they differ only by `ablations=frozenset({"no_verification"})` (`backend/app/evaluation/systems.py`).

## 4. Reading

The gate demonstrably acts: it changes the released answer on 52 of 255 cases and keeps two answers from carrying a figure the evidence does not support. Every decision-level metric is nevertheless unchanged, because the scorer grades the structured decision - goal, decision, value, outcome - which the symbolic layer fixes before any model writes a word. **Unchanged decision metrics do not mean the gate has no effect on released text**; they mean this benchmark does not measure prose. Verification correctness against independent ground truth remains unmeasured and is listed as unresolved.
