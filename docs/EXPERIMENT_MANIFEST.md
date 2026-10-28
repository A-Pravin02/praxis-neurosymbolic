# Experiment manifest

Generated 2026-10-28 10:19 UTC from the artifacts on disk by `scripts/make_experiment_manifest.py`. Commit `8138fd2`.

An experiment appears here only when its per-question or per-claim records exist. **Reproducible** means the published aggregate was recomputed from those records and matched, not that the run was repeated end to end.

| Experiment | Kind | Cases | Systems | Metrics checked | Mismatched | Reproducible |
|---|---|---:|---:|---:|---:|---|
| `research/confirm/confirm` | enterprise benchmark | 255 | 5 | 20 | 0 | yes |
| `research/dev-iter1/dev` | enterprise benchmark | 153 | 7 | 27 | 0 | yes |
| `research/paper/test` | enterprise benchmark | 255 | 14 | 55 | 0 | yes |
| `research/deploy-encoder/test+raw` | enterprise benchmark (records only) | 255 | 0 | 0 | 0 | unresolved |
| `research/dev-iter0/dev+raw` | enterprise benchmark (records only) | 153 | 0 | 0 | 0 | unresolved |
| `research/gate/test+raw` | enterprise benchmark (records only) | 306 | 0 | 0 | 0 | unresolved |
| `research/gate-ablation/test+raw` | enterprise benchmark (records only) | 255 | 0 | 0 | 0 | unresolved |
| `research/gate-ablation-fixed/test+raw` | enterprise benchmark (records only) | 255 | 0 | 0 | 0 | unresolved |
| `research/gate-ablation-v3/test+raw` | enterprise benchmark (records only) | 255 | 0 | 0 | 0 | unresolved |
| `research/parser-codefix/test+raw` | enterprise benchmark (records only) | 255 | 0 | 0 | 0 | unresolved |
| `research/parser-test/test+raw` | enterprise benchmark (records only) | 255 | 0 | 0 | 0 | unresolved |
| `research/qwen2.5-1.5b/dev+raw` | enterprise benchmark (records only) | 153 | 0 | 0 | 0 | unresolved |
| `research/smoke-qwen/dev+raw` | enterprise benchmark (records only) | 4 | 0 | 0 | 0 | unresolved |
| `football/claim-gating` | from-scratch model, claim-level gating | 1056 claims | 2 | - | - | records present |

## Per-system records

### `research/confirm/confirm`

| System | Cases | Records | SHA-256 (16) |
|---|---:|---|---|
| llm_only | 255 | `eval_reports\research\confirm\confirm.llm_only.jsonl` | `49c17d4ccd22199a` |
| llm_rag | 255 | `eval_reports\research\confirm\confirm.llm_rag.jsonl` | `3166d4e6f427039b` |
| neurosymbolic | 255 | `eval_reports\research\confirm\confirm.neurosymbolic.jsonl` | `2fe355779b5f9d65` |
| ns_no_authorization | 255 | `eval_reports\research\confirm\confirm.ns_no_authorization.jsonl` | `42fca174538c9f07` |
| ns_parser | 255 | `eval_reports\research\confirm\confirm.ns_parser.jsonl` | `be58e6cbdd8cbb15` |

### `research/dev-iter1/dev`

| System | Cases | Records | SHA-256 (16) |
|---|---:|---|---|
| neurosymbolic | 153 | `eval_reports\research\dev-iter1\dev.neurosymbolic.jsonl` | `14bd120a824ea3ae` |
| neurosymbolic_lexical | 153 | `eval_reports\research\dev-iter1\dev.neurosymbolic_lexical.jsonl` | `1698a33b31dabe57` |
| ns_latest_version | 153 | `eval_reports\research\dev-iter1\dev.ns_latest_version.jsonl` | `2de5d62ce43b5241` |
| ns_no_authorization | 153 | `eval_reports\research\dev-iter1\dev.ns_no_authorization.jsonl` | `0b0a55f1e9cc6915` |
| ns_no_verification | 153 | `eval_reports\research\dev-iter1\dev.ns_no_verification.jsonl` | `a169cabca73699d9` |
| ns_trust_statements | 153 | `eval_reports\research\dev-iter1\dev.ns_trust_statements.jsonl` | `afd96fa6d857895a` |
| ns_two_valued | 153 | `eval_reports\research\dev-iter1\dev.ns_two_valued.jsonl` | `5771a5b66421f2ba` |

### `research/paper/test`

| System | Cases | Records | SHA-256 (16) |
|---|---:|---|---|
| llm_only | 255 | `eval_reports\research\paper\test.llm_only.jsonl` | `8555239fc827b1ac` |
| llm_rag | 255 | `eval_reports\research\paper\test.llm_rag.jsonl` | `1778f2beaf22e870` |
| llm_rag_rules | 255 | `eval_reports\research\paper\test.llm_rag_rules.jsonl` | `f7f90753e8001a2d` |
| llm_rag_selfcheck | 255 | `eval_reports\research\paper\test.llm_rag_selfcheck.jsonl` | `8628f07ac866bf9d` |
| neurosymbolic | 255 | `eval_reports\research\paper\test.neurosymbolic.jsonl` | `81e214c8aeb08595` |
| ns_latest_version | 255 | `eval_reports\research\paper\test.ns_latest_version.jsonl` | `09ad6dcc1d3cd35f` |
| ns_no_authorization | 255 | `eval_reports\research\paper\test.ns_no_authorization.jsonl` | `806c7a990dc860ad` |
| ns_no_kind_agreement | 255 | `eval_reports\research\paper\test.ns_no_kind_agreement.jsonl` | `f7e510b9919321e4` |
| ns_no_number_check | 255 | `eval_reports\research\paper\test.ns_no_number_check.jsonl` | `80d5ccb72b5069cf` |
| ns_no_verification | 255 | `eval_reports\research\paper\test.ns_no_verification.jsonl` | `18e9a7a0875d9c3a` |
| ns_parser | 255 | `eval_reports\research\paper\test.ns_parser.jsonl` | `908cb373b9879168` |
| ns_trust_statements | 255 | `eval_reports\research\paper\test.ns_trust_statements.jsonl` | `706af92363e777e9` |
| ns_two_valued | 255 | `eval_reports\research\paper\test.ns_two_valued.jsonl` | `b5f7ea4cd60a6237` |
| rag_extractive | 255 | `eval_reports\research\paper\test.rag_extractive.jsonl` | `63d6b80704e6d30d` |

## Secondary study: from-scratch model

- Model: 4,262,144 parameters, best validation loss 0.7742
- Corpus: 292,948 training tokens, vocabulary 4096
- Claims: 1,091 produced, 1,056 checkable
- Records: `artifacts\football\results\claims.jsonl`

**Caveat.** the gated hallucination rate is zero by construction: only graph-supported claims are released, so this is not independent evidence of verifier precision.

## Unresolved

- Independent verification quality. The verifier is correct by construction with respect to its own graph; precision and recall against externally authored labels are unmeasured.
- Question relevance. No relevance gate is implemented, so a supported answer to a different question is not detected.
- Reference metadata. Bibliographic entries are formatted from known venues; DOIs and page numbers have not been checked against publisher records.
