"""Training loop for the from-scratch language model.

Implements what Phase 2.4 of the specification requires and nothing fancier:
causal next-token prediction, mixed precision, gradient accumulation, gradient
clipping, a cosine schedule with warmup, periodic checkpointing with optimizer
state, resume-from-checkpoint, early stopping against a documented budget, and
measurement of loss, perplexity, throughput, GPU memory and wall-clock time.

Every metric written to `metrics.jsonl` is measured during the run. Nothing is
extrapolated, and a run that ends early keeps whatever it actually produced.
"""
from __future__ import annotations

import inspect
import json
import math
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import torch

from app.slm.data import load_tokens
from app.slm.model import LanguageModel, ModelConfig


@dataclass
class TrainConfig:
    # data
    data_dir: Path = Path("artifacts/slm/data")
    out_dir: Path = Path("artifacts/slm/runs/default")

    # optimization
    batch_size: int = 16              # sequences per micro-step
    grad_accum_steps: int = 8         # effective batch = batch_size * grad_accum_steps
    learning_rate: float = 3e-4
    min_learning_rate: float = 3e-5
    weight_decay: float = 0.1
    beta1: float = 0.9
    beta2: float = 0.95
    grad_clip: float = 1.0

    # schedule
    max_steps: int = 4000
    warmup_steps: int = 200

    # evaluation / checkpointing
    eval_interval: int = 200
    eval_batches: int = 50
    checkpoint_interval: int = 500
    log_interval: int = 10

    # budget and early stopping
    max_minutes: float | None = None  # stop cleanly when the budget is spent
    early_stopping_patience: int = 5  # evaluations without improvement; 0 disables

    # runtime
    device: str = "cuda"
    amp_dtype: str = "bfloat16"       # bfloat16 needs no loss scaling
    compile_model: bool = False       # torch.compile is unreliable on Windows
    seed: int = 1337
    grad_checkpointing: bool = False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["data_dir"] = str(self.data_dir)
        payload["out_dir"] = str(self.out_dir)
        return payload


class TokenBatcher:
    """Samples fixed-length windows from a flat token stream.

    Random offsets rather than a sequential cursor: a small corpus would otherwise be
    traversed in the same order every epoch, and the model would learn the order.
    """

    def __init__(self, tokens: np.ndarray, block_size: int, batch_size: int,
                 device: str, seed: int = 0) -> None:
        if len(tokens) < block_size + 1:
            raise ValueError(
                f"token stream of {len(tokens)} is shorter than block_size+1 "
                f"({block_size + 1}); prepare more data or reduce block_size")
        self.tokens = tokens
        self.block_size = block_size
        self.batch_size = batch_size
        self.device = device
        self.rng = np.random.default_rng(seed)

    def __call__(self) -> tuple[torch.Tensor, torch.Tensor]:
        high = len(self.tokens) - self.block_size - 1
        offsets = self.rng.integers(0, high, size=self.batch_size)
        # int64 for the embedding lookup; the stream itself is uint16.
        x = np.stack([self.tokens[o:o + self.block_size] for o in offsets]).astype(np.int64)
        y = np.stack([self.tokens[o + 1:o + 1 + self.block_size] for o in offsets]).astype(np.int64)
        xt = torch.from_numpy(x)
        yt = torch.from_numpy(y)
        if self.device.startswith("cuda"):
            # pin + non_blocking overlaps the copy with compute
            xt = xt.pin_memory().to(self.device, non_blocking=True)
            yt = yt.pin_memory().to(self.device, non_blocking=True)
        else:
            xt, yt = xt.to(self.device), yt.to(self.device)
        return xt, yt


def build_optimizer(model: LanguageModel, cfg: TrainConfig) -> torch.optim.Optimizer:
    """AdamW with decay on matrices only.

    Norm gains and biases are 1-D and excluded: decaying them pulls the
    normalization scale toward zero, which hurts rather than regularizes.
    """
    decay, no_decay, seen = [], [], set()
    for name, param in model.named_parameters():
        if not param.requires_grad or id(param) in seen:
            continue
        seen.add(id(param))
        (decay if param.dim() >= 2 else no_decay).append(param)
    groups = [
        {"params": decay, "weight_decay": cfg.weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]
    # Inspect the signature, not the docstring: docstrings are absent under -OO.
    supports_fused = "fused" in inspect.signature(torch.optim.AdamW).parameters
    fused = cfg.device.startswith("cuda") and supports_fused
    return torch.optim.AdamW(groups, lr=cfg.learning_rate,
                             betas=(cfg.beta1, cfg.beta2),
                             **({"fused": True} if fused else {}))


def lr_at(step: int, cfg: TrainConfig) -> float:
    """Linear warmup, then cosine decay to `min_learning_rate`."""
    if step < cfg.warmup_steps:
        return cfg.learning_rate * (step + 1) / max(1, cfg.warmup_steps)
    progress = (step - cfg.warmup_steps) / max(1, cfg.max_steps - cfg.warmup_steps)
    progress = min(1.0, max(0.0, progress))
    coeff = 0.5 * (1.0 + math.cos(math.pi * progress))
    return cfg.min_learning_rate + coeff * (cfg.learning_rate - cfg.min_learning_rate)


@torch.no_grad()
def estimate_loss(model: LanguageModel, batcher: TokenBatcher, batches: int,
                  autocast_ctx) -> float:
    model.eval()
    losses = torch.zeros(batches)
    for i in range(batches):
        x, y = batcher()
        with autocast_ctx:
            _, loss = model(x, y)
        losses[i] = loss.float().item()
    model.train()
    return losses.mean().item()


def perplexity(loss: float) -> float | None:
    """exp(loss), or None when it would overflow.

    Perplexity is only meaningful for a converged token-level cross-entropy; an
    untrained model can produce a loss whose exponential is not representable, and
    reporting `inf` as a result would be misleading.
    """
    if loss > 20:
        return None
    return math.exp(loss)


def _autocast(cfg: TrainConfig):
    if not cfg.device.startswith("cuda"):
        return torch.autocast("cpu", dtype=torch.bfloat16, enabled=False)
    dtype = {"bfloat16": torch.bfloat16, "float16": torch.float16}[cfg.amp_dtype]
    if dtype is torch.bfloat16 and not torch.cuda.is_bf16_supported():
        dtype = torch.float16
    return torch.autocast("cuda", dtype=dtype)


def save_checkpoint(path: Path, model: LanguageModel, optimizer, step: int,
                    best_val: float, model_cfg: ModelConfig, train_cfg: TrainConfig,
                    history: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save({
        "model": model.state_dict(),
        "optimizer": optimizer.state_dict(),
        "step": step,
        "best_val_loss": best_val,
        "model_config": model_cfg.to_dict(),
        "train_config": train_cfg.to_dict(),
        "history": history,
        # Recorded in the checkpoint itself so a file on disk carries its own
        # evidence that it is not a fine-tune of someone else's weights.
        "provenance": model.parameter_provenance(),
    }, path)


def load_checkpoint(path: Path, model: LanguageModel, optimizer) -> tuple[int, float, list[dict]]:
    # weights_only=True refuses to unpickle arbitrary objects. Our checkpoints hold
    # only tensors and plain containers, so this costs nothing and removes the
    # arbitrary-code-execution path that a shared checkpoint file would otherwise open.
    payload = torch.load(path, map_location="cpu", weights_only=True)
    model.load_state_dict(payload["model"])
    if optimizer is not None and payload.get("optimizer"):
        optimizer.load_state_dict(payload["optimizer"])
    return payload["step"], payload.get("best_val_loss", float("inf")), payload.get("history", [])


def train(model_cfg: ModelConfig, cfg: TrainConfig, resume: bool = False) -> dict:
    torch.manual_seed(cfg.seed)
    np.random.seed(cfg.seed)
    if cfg.device.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError("device is 'cuda' but torch reports no CUDA device")
    # TF32 costs a little precision on matmuls and buys a lot of speed on Ampere+.
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    train_tokens = load_tokens(cfg.data_dir / "train.bin")
    val_tokens = load_tokens(cfg.data_dir / "validation.bin")

    model = LanguageModel(model_cfg).to(cfg.device)
    if cfg.grad_checkpointing:
        # Trades ~30% speed for a large activation-memory saving.
        for block in model.blocks:
            block.forward = torch.utils.checkpoint.checkpoint_wrapper(  # type: ignore[attr-defined]
                block.forward) if hasattr(torch.utils.checkpoint, "checkpoint_wrapper") \
                else block.forward
    optimizer = build_optimizer(model, cfg)

    step, best_val, history = 0, float("inf"), []
    latest = cfg.out_dir / "checkpoint_latest.pt"
    if resume and latest.exists():
        step, best_val, history = load_checkpoint(latest, model, optimizer)
        print(f"resumed from {latest} at step {step} (best val {best_val:.4f})")

    train_batch = TokenBatcher(train_tokens, model_cfg.block_size, cfg.batch_size,
                               cfg.device, seed=cfg.seed + step)
    val_batch = TokenBatcher(val_tokens, model_cfg.block_size, cfg.batch_size,
                             cfg.device, seed=cfg.seed + 99991)
    autocast_ctx = _autocast(cfg)
    # float16 needs a loss scaler; bfloat16 does not.
    use_scaler = cfg.device.startswith("cuda") and cfg.amp_dtype == "float16"
    scaler = torch.amp.GradScaler("cuda", enabled=use_scaler)

    metrics_path = cfg.out_dir / "metrics.jsonl"
    (cfg.out_dir / "model_config.json").write_text(
        json.dumps(model_cfg.to_dict(), indent=2), encoding="utf-8")
    (cfg.out_dir / "train_config.json").write_text(
        json.dumps(cfg.to_dict(), indent=2), encoding="utf-8")
    (cfg.out_dir / "provenance.json").write_text(
        json.dumps(model.parameter_provenance(), indent=2), encoding="utf-8")

    tokens_per_step = cfg.batch_size * cfg.grad_accum_steps * model_cfg.block_size
    print(f"parameters: {model.num_parameters()/1e6:.2f}M "
          f"({model.num_parameters(True)/1e6:.2f}M non-embedding)")
    print(f"tokens/step: {tokens_per_step:,}  train tokens: {len(train_tokens):,}")

    model.train()
    started = time.time()
    evals_without_improvement = 0
    stop_reason = "max_steps"
    window_start, window_tokens = time.time(), 0

    while step < cfg.max_steps:
        lr = lr_at(step, cfg)
        for group in optimizer.param_groups:
            group["lr"] = lr

        optimizer.zero_grad(set_to_none=True)
        accumulated = 0.0
        for _ in range(cfg.grad_accum_steps):
            x, y = train_batch()
            with autocast_ctx:
                _, loss = model(x, y)
                # Scale so the accumulated gradient equals the full-batch mean.
                loss = loss / cfg.grad_accum_steps
            scaler.scale(loss).backward() if use_scaler else loss.backward()
            accumulated += loss.float().item()
        if cfg.grad_clip > 0:
            if use_scaler:
                scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
        if use_scaler:
            scaler.step(optimizer)
            scaler.update()
        else:
            optimizer.step()

        step += 1
        window_tokens += tokens_per_step

        if step % cfg.log_interval == 0:
            elapsed = time.time() - window_start
            tps = window_tokens / elapsed if elapsed > 0 else 0.0
            mem = (torch.cuda.max_memory_allocated() / 1e9
                   if cfg.device.startswith("cuda") else 0.0)
            record = {"step": step, "train_loss": accumulated, "lr": lr,
                      "tokens_per_second": round(tps, 1),
                      "gpu_memory_gb": round(mem, 3),
                      "elapsed_minutes": round((time.time() - started) / 60, 2)}
            history.append(record)
            with metrics_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            print(f"step {step:>6}  loss {accumulated:.4f}  lr {lr:.2e}  "
                  f"{tps:,.0f} tok/s  {mem:.2f} GB")
            window_start, window_tokens = time.time(), 0

        if step % cfg.eval_interval == 0 or step == cfg.max_steps:
            val_loss = estimate_loss(model, val_batch, cfg.eval_batches, autocast_ctx)
            ppl = perplexity(val_loss)
            record = {"step": step, "val_loss": round(val_loss, 5),
                      "val_perplexity": round(ppl, 3) if ppl else None,
                      "elapsed_minutes": round((time.time() - started) / 60, 2)}
            history.append(record)
            with metrics_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
            print(f"  eval step {step}: val_loss {val_loss:.4f}"
                  + (f"  ppl {ppl:.2f}" if ppl else "  ppl n/a (loss too high)"))

            if val_loss < best_val - 1e-4:
                best_val = val_loss
                evals_without_improvement = 0
                save_checkpoint(cfg.out_dir / "checkpoint_best.pt", model, optimizer,
                                step, best_val, model_cfg, cfg, history)
            else:
                evals_without_improvement += 1
                if (cfg.early_stopping_patience
                        and evals_without_improvement >= cfg.early_stopping_patience):
                    stop_reason = "early_stopping"
                    print(f"  stopping: {evals_without_improvement} evaluations "
                          f"without improvement")
                    break

        if step % cfg.checkpoint_interval == 0:
            save_checkpoint(latest, model, optimizer, step, best_val, model_cfg, cfg,
                            history)

        if cfg.max_minutes and (time.time() - started) / 60 >= cfg.max_minutes:
            stop_reason = "time_budget"
            print(f"  stopping: time budget of {cfg.max_minutes} min reached")
            break

    save_checkpoint(latest, model, optimizer, step, best_val, model_cfg, cfg, history)
    final_val = estimate_loss(model, val_batch, cfg.eval_batches, autocast_ctx)
    summary = {
        "steps_completed": step,
        "stop_reason": stop_reason,
        "final_val_loss": round(final_val, 5),
        "final_val_perplexity": (round(perplexity(final_val), 3)
                                 if perplexity(final_val) else None),
        "best_val_loss": round(best_val, 5),
        "best_val_perplexity": (round(perplexity(best_val), 3)
                                if perplexity(best_val) else None),
        "tokens_processed": step * tokens_per_step,
        "wall_clock_minutes": round((time.time() - started) / 60, 2),
        "peak_gpu_memory_gb": (round(torch.cuda.max_memory_allocated() / 1e9, 3)
                               if cfg.device.startswith("cuda") else None),
        "parameters": model.num_parameters(),
        "non_embedding_parameters": model.num_parameters(non_embedding=True),
        "checkpoint_bytes": (latest.stat().st_size if latest.exists() else None),
        "device": (torch.cuda.get_device_name(0)
                   if cfg.device.startswith("cuda") else "cpu"),
    }
    (cfg.out_dir / "summary.json").write_text(json.dumps(summary, indent=2),
                                              encoding="utf-8")
    print("\nsummary:", json.dumps(summary, indent=2))
    return summary
