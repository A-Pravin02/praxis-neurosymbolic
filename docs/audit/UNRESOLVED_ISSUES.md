# Unresolved issues

What this revision could not settle from the available evidence, and what would settle
each. Nothing here is a guess presented as a finding; where a number could not be
confirmed, the original is retained and flagged rather than replaced.

---

## 1. Reference [2]: volume and issue not independently confirmed

The report cites Huang et al., *A survey on hallucination in large language models*,
ACM Trans. Inf. Syst., **vol. 43, no. 2**, 2025.

The DOI (`10.1145/3703155`), the venue, the title and the author list were confirmed
against Crossref-derived records. The **volume and issue were not**: the records
reachable gave the DOI and an online-first date of November 2024 without final issue
assignment.

*Retained as cited, because it is what the entry already said and nothing contradicts
it.* To settle: open the DOI landing page or the ACM Digital Library entry and read the
final volume, issue and article number.

Verified exactly, against publisher records: [1], [5], [6], [7], [8], [9], [10], [12] —
authors, titles, venues, years, page ranges and DOIs all match. Entries [3], [4], [11]
and [13] make no page-range claim (NeurIPS and ICLR proceedings do not paginate, and
[13] is a monograph), so there is nothing in them to be wrong.

## 2. Whether each cited work supports the exact limitation attributed to it

Table 1 attributes a specific limitation to each family of prior work, and Table 2
characterises six capability dimensions across them. These were written from the
methods' published descriptions and are phrased to describe *scope* rather than
deficiency — Table 2's caption says so explicitly.

They have **not** been checked line by line against each paper. The risk is low for the
dimensions that follow from a method's definition (RAG does not perform symbolic
reasoning; a self-critique loop uses the same model twice) and higher for the finer
claims, in particular the characterisation of Self-RAG's ability to decline.

To settle: read each of the eleven cited systems' papers against the row attributed to
it, and correct or soften any row that overstates.

## 3. Screenshot resolution cannot be raised with the tools available

The five interface screenshots are 800×550, the fixed capture size of the browser pane
used to take them. Enlarging them does not add detail.

What was done instead: the repeated sidebar and title bar were cropped from the four
case screenshots, enlarging the informative panel by 1.25× at the same printed width,
which makes the check names, verdicts and released answers legible in print. Aspect
ratios are preserved.

To settle properly: retake the captures from a browser at a larger window size and
re-run `scripts/crop_screenshots.py`.

## 4. The serving device and quantization for the 7B baselines were never recorded

The run metadata for the language-model baselines records the model name and the
provider, but not the device the model was generated on, nor whether it was loaded in
4-bit. `scripts/local_llm_server.py` supports a `--4bit` flag; whether it was used for
the reported runs is not recoverable from the artifacts.

This matters for one claim only — the latency comparison — and the report now states in
Table 6 and in the conclusion that the comparison is **not hardware-controlled**. The
accuracy and hallucination results do not depend on it.

To settle: re-run the baselines with the device and quantization recorded in the run
metadata, which also requires the 7B weights, which are not on this machine (see 5).

## 5. The 7B model is not on this machine, so the parser configuration cannot be re-run

Every `ns_parser` figure in the report — 0.945 test, 0.835 confirmatory — comes from
stored records produced with Qwen2.5-7B-Instruct. Only the 1.5B is present now.

Consequence: the relevance gate described in Section 7.2 was measured on the **1.5B**
configuration (6 of 15 → 15 of 15 not-covered, nine gained and none lost; nothing lost
on the curated suite either), and that result is reported as the 1.5B's. **No published
7B number was changed**, and the gate's effect on the 7B configuration is unmeasured.

To settle: obtain the 7B weights and re-run `scripts/run_research.py --systems ns_parser`
on both splits.

## 6. Verifier precision and recall are unmeasured

The verifier is correct by construction with respect to its own rule base. Whether that
rule base faithfully encodes the policy text is checked only by `scripts/check_oracle.py`
comparing it against a second implementation written by the same person.

To settle: independent ground-truth labels, authored without reference to the rule base.
This is stated as a limitation in Section 9 and is not claimed as resolved.

## 7. Bootstrap intervals ignore clustering by world

Questions are generated within worlds and share each world's policy parameters and
records, so they are not independent. The reported intervals resample questions and are
therefore narrower than a world-clustered analysis would give.

This is now stated in Section 8.2 and in the limitations rather than left implicit. To
settle: a cluster bootstrap resampling worlds, which with five test worlds would give
intervals wide enough that most between-system comparisons would become uninformative —
which is itself the honest finding, and the reason it is worth doing.

## 8. Document length

The report is **19 pages** against the 12–15 page guideline. The growth is accounted for
by the sections this revision was asked to add: the capability comparison table, the
twelve-check table, five methodology subsections, the environment table, the statistical
treatment section with its interval table, the transfer figure, the deployment-risk
subsection, and the expanded appendix.

Two pages were already reclaimed by removing forced page breaks that had stranded
figures on near-empty pages, and by resizing images. Reducing further means cutting
requested content, which is a decision for the author rather than one to make silently.
