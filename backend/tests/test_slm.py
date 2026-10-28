"""Tests for the from-scratch language model, its data pipeline and its training loop.

These are marked `no_db` deliberately: the model and its corpus pipeline are pure
computation, and needing a database to test them would make them unverifiable exactly
when a database is unavailable.

The tests that matter most for the research claim are the ones asserting that the model
really is randomly initialized and that the corpus really is free of evaluation data.
Those two properties are what "trained from scratch on documented data" means, and
neither is self-evident from reading the code.
"""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

torch = pytest.importorskip("torch", reason="torch is only needed for the SLM experiment")

from app.slm import data as D
from app.slm.model import (LanguageModel, ModelConfig, PRESETS, apply_rope,
                           build_rope_cache)
from app.slm.train import TokenBatcher, TrainConfig, lr_at, perplexity

pytestmark = pytest.mark.no_db


# --------------------------------------------------------------------------
# the from-scratch claim
# --------------------------------------------------------------------------

def test_config_refuses_to_declare_pretrained_initialization():
    """The one thing this experiment may not do is start from someone else's weights."""
    with pytest.raises(ValueError, match="random"):
        ModelConfig(initialized_from="Qwen/Qwen2.5-1.5B-Instruct")


def test_provenance_reports_random_initialization():
    model = LanguageModel(PRESETS["tiny"])
    provenance = model.parameter_provenance()
    assert provenance["initialized_from"] == "random"
    assert provenance["pretrained_weights_loaded"] is False
    assert provenance["trainable_parameters"] == model.num_parameters()
    assert "normal(0, 0.02)" in provenance["init_distribution"]


def test_untrained_loss_matches_a_uniform_distribution():
    """A randomly initialized LM must score ~ln(vocab_size) on next-token prediction.

    This is the sharpest available check that no pretrained knowledge leaked in: a model
    with any real language ability would score materially below ln(V) immediately.
    """
    cfg = ModelConfig(vocab_size=8192, n_layer=2, n_head=4, n_embd=128, block_size=128)
    torch.manual_seed(0)
    model = LanguageModel(cfg)
    sequence = torch.randint(0, cfg.vocab_size, (8, cfg.block_size + 1))
    _, loss = model(sequence[:, :-1], sequence[:, 1:])
    assert abs(loss.item() - math.log(cfg.vocab_size)) < 0.25


def test_tied_embeddings_share_storage():
    model = LanguageModel(PRESETS["tiny"])
    assert model.lm_head.weight is model.token_embedding.weight
    # Counted once, not twice, or the reported parameter count would be inflated.
    direct = sum(p.numel() for p in {id(p): p for p in model.parameters()}.values())
    assert model.num_parameters() == direct


# --------------------------------------------------------------------------
# architecture mechanics
# --------------------------------------------------------------------------

def test_attention_is_causal():
    """Changing a later token must not alter an earlier position's output.

    If this fails the model can see its own targets and every loss number is worthless.
    """
    cfg = ModelConfig(vocab_size=256, n_layer=2, n_head=2, n_embd=64, block_size=16)
    torch.manual_seed(0)
    model = LanguageModel(cfg).eval()
    a = torch.randint(0, cfg.vocab_size, (1, 8))
    b = a.clone()
    b[0, -1] = (b[0, -1] + 1) % cfg.vocab_size      # perturb only the final token
    with torch.no_grad():
        # Compare full hidden states up to the penultimate position.
        ha = model.norm_final(_hidden(model, a))[:, :-1]
        hb = model.norm_final(_hidden(model, b))[:, :-1]
    assert torch.allclose(ha, hb, atol=1e-5)


def _hidden(model: LanguageModel, idx: torch.Tensor) -> torch.Tensor:
    x = model.token_embedding(idx)
    for block in model.blocks:
        x = block(x, model.rope_cos, model.rope_sin)
    return x


def test_rope_preserves_norm_and_is_position_dependent():
    cos, sin = build_rope_cache(16, 8, 10000.0)
    x = torch.randn(1, 2, 4, 8)
    rotated = apply_rope(x, cos, sin)
    # A rotation changes direction, never length.
    assert torch.allclose(x.norm(dim=-1), rotated.norm(dim=-1), atol=1e-5)
    # Identical content at different positions must encode differently.
    assert not torch.allclose(rotated[:, :, 0], rotated[:, :, 1])


def test_rope_rejects_odd_head_dim():
    with pytest.raises(ValueError, match="even"):
        build_rope_cache(8, 7, 10000.0)


def test_block_size_is_enforced():
    cfg = PRESETS["tiny"]
    model = LanguageModel(cfg)
    too_long = torch.zeros((1, cfg.block_size + 1), dtype=torch.long)
    with pytest.raises(ValueError, match="exceeds block_size"):
        model(too_long)


def test_config_rejects_indivisible_head_split():
    with pytest.raises(ValueError, match="divisible"):
        ModelConfig(n_embd=100, n_head=8)


def test_greedy_generation_is_deterministic():
    """Benchmarks must be repeatable on the same hardware."""
    torch.manual_seed(0)
    model = LanguageModel(PRESETS["tiny"])
    prompt = torch.zeros((1, 4), dtype=torch.long)
    first = model.generate(prompt, max_new_tokens=6, temperature=0)
    second = model.generate(prompt, max_new_tokens=6, temperature=0)
    assert torch.equal(first, second)


# --------------------------------------------------------------------------
# data pipeline
# --------------------------------------------------------------------------

def test_split_assignment_is_deterministic_and_content_addressed():
    text = "a document about returns policy and delivery windows " * 4
    assert D.assign_split(text) == D.assign_split(text)
    # Membership follows the content, not the position in the stream.
    assert D.assign_split(text) == D.assign_split(" ".join(text.split()))


def test_split_proportions_are_roughly_respected():
    rng = np.random.default_rng(0)
    counts = {"train": 0, "validation": 0, "test": 0}
    for _ in range(20000):
        counts[D.assign_split(str(rng.integers(0, 1 << 62)))] += 1
    assert counts["train"] / 20000 > 0.97
    assert counts["validation"] > 0 and counts["test"] > 0


def test_cleaning_drops_headings_and_fragments():
    assert D.clean_document(" = Heading = ") is None
    assert D.clean_document("   ") is None
    assert D.clean_document("tiny") is None
    cleaned = D.clean_document("A sentence long enough to survive the length filter.")
    assert cleaned and "sentence" in cleaned


def test_cleaning_removes_unknown_token_markers():
    cleaned = D.clean_document("The <unk> policy applies to every order placed today.")
    assert cleaned is not None and "<unk>" not in cleaned


def test_shingle_hash_is_stable_across_processes():
    """Reproducible dedup requires a hash that does not change between interpreters."""
    import subprocess, sys, textwrap
    script = textwrap.dedent("""
        import sys; sys.path.insert(0, %r)
        from app.slm.data import _stable_hash
        print(_stable_hash("the quick brown fox"))
    """) % str(__import__("pathlib").Path(D.__file__).parents[2])
    runs = {subprocess.run([sys.executable, "-c", script], capture_output=True,
                           text=True, env={"PYTHONHASHSEED": seed}).stdout.strip()
            for seed in ("0", "1", "random")}
    assert len(runs) == 1, f"hash varied across PYTHONHASHSEED values: {runs}"


def test_near_duplicate_filter_catches_a_reworded_header():
    body = (" ".join(f"sentence number {i} about delivery and refunds" for i in range(40)))
    f = D.NearDuplicateFilter()
    assert f.is_duplicate(body) is False          # first sighting
    assert f.is_duplicate(body) is True           # exact repeat
    assert f.is_duplicate("Published 2024. " + body) is True   # trivially altered
    assert f.is_duplicate("completely unrelated text " * 40) is False


def test_leakage_check_finds_a_planted_benchmark_question():
    probe = "Can I return the shoes I bought last week after they arrived damaged"
    corpus = ["an unrelated encyclopedia paragraph about trains and timetables",
              f"some preamble ... {probe} ... some trailing text"]
    hits = D.check_leakage(corpus, [probe])
    assert len(hits) == 1 and hits[0]["document_index"] == 1


def test_leakage_check_is_clean_on_unrelated_text():
    probe = "Can I return the shoes I bought last week after they arrived damaged"
    assert D.check_leakage(["a paragraph about geology and rock formation"], [probe]) == []


def test_leakage_check_ignores_probes_too_short_to_be_evidence():
    """A handful of common words would match innocently and make the check noise."""
    assert D.check_leakage(["the policy is clear"], ["the policy"]) == []


def test_pack_and_reload_tokens_roundtrip(tmp_path):
    out = tmp_path / "train.bin"
    total = D.pack_tokens([[1, 2, 3], [4, 5]], out, eos_id=9)
    assert total == 5 + 2                             # two documents, two EOS
    stream = D.load_tokens(out)
    assert list(stream) == [1, 2, 3, 9, 4, 5, 9]
    assert stream.dtype == D.TOKEN_DTYPE


def test_declared_corpora_carry_licence_and_source():
    """Provenance is a research requirement, not documentation polish."""
    for spec in D.CORPORA.values():
        assert spec.licence and spec.source_url.startswith("https://")
        assert spec.description


# --------------------------------------------------------------------------
# training loop
# --------------------------------------------------------------------------

def test_batcher_shifts_targets_by_exactly_one():
    tokens = np.arange(500, dtype=np.uint16)
    batcher = TokenBatcher(tokens, block_size=8, batch_size=4, device="cpu", seed=0)
    x, y = batcher()
    assert x.shape == y.shape == (4, 8)
    # y must be x advanced one step, or the model learns the identity function.
    assert torch.equal(x[:, 1:], y[:, :-1])


def test_batcher_refuses_a_stream_shorter_than_the_context():
    with pytest.raises(ValueError, match="shorter than block_size"):
        TokenBatcher(np.arange(4, dtype=np.uint16), block_size=8, batch_size=1,
                     device="cpu")


def test_learning_rate_warms_up_then_decays():
    cfg = TrainConfig(learning_rate=1e-3, min_learning_rate=1e-4,
                      warmup_steps=10, max_steps=100)
    assert lr_at(0, cfg) == pytest.approx(1e-4, rel=0.01)      # first step of warmup
    assert lr_at(9, cfg) == pytest.approx(1e-3, rel=0.01)      # peak at end of warmup
    assert lr_at(99, cfg) < lr_at(50, cfg) < lr_at(9, cfg)     # monotone decay after
    assert lr_at(99, cfg) >= cfg.min_learning_rate


def test_perplexity_declines_to_report_an_overflowing_value():
    """Reporting inf as a perplexity would be misleading, so it reports nothing."""
    assert perplexity(2.0) == pytest.approx(math.exp(2.0))
    assert perplexity(50.0) is None


def test_one_optimization_step_reduces_loss_on_a_memorizable_batch():
    """An end-to-end check that gradients flow and the optimizer is wired correctly."""
    from app.slm.train import build_optimizer
    cfg = ModelConfig(vocab_size=128, n_layer=2, n_head=2, n_embd=64, block_size=32)
    torch.manual_seed(0)
    model = LanguageModel(cfg).train()
    optimizer = build_optimizer(model, TrainConfig(device="cpu", learning_rate=1e-2))
    sequence = torch.randint(0, cfg.vocab_size, (4, cfg.block_size + 1))
    x, y = sequence[:, :-1], sequence[:, 1:]
    _, first = model(x, y)
    for _ in range(30):
        _, loss = model(x, y)
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        optimizer.step()
    _, final = model(x, y)
    assert final.item() < first.item() - 0.5


def test_checkpoint_roundtrip_preserves_weights_and_step(tmp_path):
    from app.slm.train import build_optimizer, load_checkpoint, save_checkpoint
    cfg = PRESETS["tiny"]
    torch.manual_seed(0)
    model = LanguageModel(cfg)
    optimizer = build_optimizer(model, TrainConfig(device="cpu"))
    path = tmp_path / "ckpt.pt"
    save_checkpoint(path, model, optimizer, step=42, best_val=1.23,
                    model_cfg=cfg, train_cfg=TrainConfig(device="cpu"), history=[])

    torch.manual_seed(1)                       # different initialization
    restored = LanguageModel(cfg)
    assert not torch.equal(restored.token_embedding.weight,
                           model.token_embedding.weight)
    step, best, _ = load_checkpoint(path, restored, None)
    assert step == 42 and best == pytest.approx(1.23)
    assert torch.equal(restored.token_embedding.weight, model.token_embedding.weight)


def test_preset_parameter_counts_are_in_the_documented_range():
    """The brief asks for roughly 10-50M parameters; `small` is the reported run."""
    assert 10e6 < PRESETS["pilot"].vocab_size * 0 + \
        LanguageModel(PRESETS["pilot"]).num_parameters() < 50e6
    assert 10e6 < LanguageModel(PRESETS["small"]).num_parameters() < 50e6
