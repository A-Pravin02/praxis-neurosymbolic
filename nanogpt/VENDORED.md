# nanoGPT, vendored

Source: https://github.com/karpathy/nanoGPT (MIT licence, `LICENSE` retained).
Copied verbatim from `nanoGPT-master` on 2026-10-26. `model.py`, `train.py`,
`sample.py`, `configurator.py` and `bench.py` are unmodified.

It is vendored rather than referenced so the paper's artifact is self-contained and a
reviewer can reproduce the run without fetching a moving upstream.

PRAXIS-specific additions live outside these files:

* `config/train_football.py` - a configuration sized for a 6 GB GPU.
* `scripts/prepare_football_data.py` - builds `train.bin`/`val.bin` and the symbolic
  knowledge pack from one structured dataset, so the model and the verifier are
  grounded in the same facts.

Why the data pipeline is ours rather than nanoGPT's `data/*/prepare.py`: the research
claim needs a corpus with recorded provenance, deduplication, deterministic splits and
an evaluation-leakage check that fails the build. nanoGPT's preparers are deliberately
minimal and do none of that.
