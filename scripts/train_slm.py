"""Train the from-scratch language model.

    # what fits, and how fast, before committing to a long run
    python scripts/train_slm.py --list-configs

    # short pilot: measures throughput, memory and loss stability
    python scripts/train_slm.py --preset pilot --max-steps 200 \
        --data artifacts/slm/data --out artifacts/slm/runs/pilot

    # the reported run
    python scripts/train_slm.py --preset small --max-steps 6000 \
        --data artifacts/slm/data --out artifacts/slm/runs/small

    # resume an interrupted run from its last checkpoint
    python scripts/train_slm.py --preset small --out artifacts/slm/runs/small --resume

Writes into `--out`: model_config.json, train_config.json, provenance.json,
metrics.jsonl, checkpoint_latest.pt, checkpoint_best.pt and summary.json.

A run that stops early for any reason - time budget, early stopping, interruption -
keeps whatever it genuinely produced. Nothing is extrapolated.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.slm.model import PRESETS, LanguageModel, ModelConfig  # noqa: E402
from app.slm.train import TrainConfig, train  # noqa: E402


def list_configs() -> None:
    import torch
    print(f"{'preset':8s} {'params':>10s} {'non-emb':>10s} {'layers':>7s} "
          f"{'d_model':>8s} {'heads':>6s} {'ctx':>5s} {'vocab':>7s}")
    for name, cfg in PRESETS.items():
        model = LanguageModel(cfg)
        print(f"{name:8s} {model.num_parameters()/1e6:9.2f}M "
              f"{model.num_parameters(True)/1e6:9.2f}M {cfg.n_layer:7d} "
              f"{cfg.n_embd:8d} {cfg.n_head:6d} {cfg.block_size:5d} {cfg.vocab_size:7d}")
    if torch.cuda.is_available():
        props = torch.cuda.get_device_properties(0)
        print(f"\ndevice: {props.name}, {props.total_memory/1e9:.2f} GB VRAM")
        print("Note: on Windows, exceeding VRAM does not raise OutOfMemoryError - the "
              "driver spills to host memory and throughput collapses instead. Judge "
              "feasibility by tokens/s, not by peak memory alone.")
    else:
        print("\nno CUDA device visible; training would run on CPU")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-configs", action="store_true",
                    help="print parameter counts for every preset and exit")
    ap.add_argument("--preset", default="small", choices=sorted(PRESETS))
    ap.add_argument("--data", type=Path, default=Path("artifacts/slm/data"))
    ap.add_argument("--out", type=Path, default=None,
                    help="defaults to artifacts/slm/runs/<preset>")
    ap.add_argument("--resume", action="store_true",
                    help="continue from checkpoint_latest.pt in --out")

    ap.add_argument("--max-steps", type=int, default=None)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--grad-accum", type=int, default=None)
    ap.add_argument("--block-size", type=int, default=None,
                    help="override the preset's context length")
    ap.add_argument("--learning-rate", type=float, default=None)
    ap.add_argument("--warmup-steps", type=int, default=None)
    ap.add_argument("--eval-interval", type=int, default=None)
    ap.add_argument("--checkpoint-interval", type=int, default=None)
    ap.add_argument("--max-minutes", type=float, default=None,
                    help="stop cleanly once this much wall-clock has been spent")
    ap.add_argument("--patience", type=int, default=None,
                    help="evaluations without improvement before stopping; 0 disables")
    ap.add_argument("--device", default=None)
    ap.add_argument("--amp-dtype", default=None, choices=["bfloat16", "float16"])
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    if args.list_configs:
        list_configs()
        return

    model_cfg = ModelConfig(**PRESETS[args.preset].to_dict())
    if args.block_size:
        model_cfg = ModelConfig(**{**model_cfg.to_dict(), "block_size": args.block_size})

    # The tokenizer decides the vocabulary, not the preset: a mismatch would silently
    # train a model whose embedding table cannot represent the data.
    manifest_path = args.data / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        actual = manifest["tokenizer"]["vocab_size"]
        if actual != model_cfg.vocab_size:
            print(f"adjusting vocab_size {model_cfg.vocab_size} -> {actual} "
                  f"to match the prepared tokenizer")
            model_cfg = ModelConfig(**{**model_cfg.to_dict(), "vocab_size": actual})
    else:
        print(f"warning: no manifest at {manifest_path}; "
              f"assuming vocab_size={model_cfg.vocab_size}")

    out_dir = args.out or Path("artifacts/slm/runs") / args.preset
    overrides = {
        "data_dir": args.data, "out_dir": out_dir,
        "max_steps": args.max_steps, "batch_size": args.batch_size,
        "grad_accum_steps": args.grad_accum, "learning_rate": args.learning_rate,
        "warmup_steps": args.warmup_steps, "eval_interval": args.eval_interval,
        "checkpoint_interval": args.checkpoint_interval,
        "max_minutes": args.max_minutes, "early_stopping_patience": args.patience,
        "device": args.device, "amp_dtype": args.amp_dtype, "seed": args.seed,
    }
    train_cfg = TrainConfig(**{k: v for k, v in overrides.items() if v is not None})

    print(f"preset {args.preset}: {model_cfg.n_layer} layers, d_model {model_cfg.n_embd}, "
          f"{model_cfg.n_head} heads, context {model_cfg.block_size}, "
          f"vocab {model_cfg.vocab_size}")
    train(model_cfg, train_cfg, resume=args.resume)


if __name__ == "__main__":
    main()
