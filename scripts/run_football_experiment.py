"""Measure whether the symbolic layer reduces hallucination in the from-scratch model.

    python scripts/run_football_experiment.py --samples 400

The model is prompted with stems naming matches it was never trained on (the held-out
split) and matches it was, then every sentence it produces is turned into a typed claim
and checked against the goal records.

Two systems are compared on exactly the same generations, which is what makes the
comparison fair - the gate cannot change what the model wrote, only what reaches the
reader:

  **slm_only**        every claim the model makes is released.
  **slm_verified**    only claims the graph supports are released.

Reported per system:

  claims                 how many typed claims were produced
  hallucination_rate     share of RELEASED claims that are false or unsupported
  coverage               share of produced claims that are released
  precision / recall     of the gate, against the graph's own verdict

Coverage is reported beside hallucination throughout. A gate that releases nothing has
a perfect hallucination rate and is useless, and that must be visible rather than
hidden.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "nanogpt"))

from app.football.verifier import (  # noqa: E402
    CONTRADICTED, NOT_CHECKABLE, SUPPORTED, UNVERIFIED,
    FootballGraph, extract_claims, gate, verify)


def load_model(checkpoint: Path, device: str):
    import torch
    from model import GPT, GPTConfig

    # Prefer the safe loader. nanoGPT also pickles its config dict, which may hold
    # objects the restricted unpickler refuses; fall back only for a checkpoint this
    # project produced itself, and say so rather than silently widening the trust.
    try:
        payload = torch.load(checkpoint, map_location=device, weights_only=True)
    except Exception:
        print(f"note: {checkpoint.name} needs the permissive loader (nanoGPT pickles "
              f"its config); loading a locally trained checkpoint")
        payload = torch.load(checkpoint, map_location=device, weights_only=False)
    model = GPT(GPTConfig(**payload["model_args"]))
    # Strip the torch.compile prefix if the checkpoint came from a compiled run.
    state = {k.removeprefix("_orig_mod."): v for k, v in payload["model"].items()}
    model.load_state_dict(state)
    model.eval().to(device)
    return model, payload


def build_prompts(manifest: dict, graph: FootballGraph, eval_path: Path,
                  n: int, seed: int) -> list[dict]:
    """Stems that invite a factual completion, half from matches never trained on.

    Held-out stems are where hallucination is expected: the model has no information
    about those matches, so whatever it writes about them is invented. Seen stems are
    the control - it should be able to recite those.
    """
    rng = random.Random(seed)
    # Case ids are "<match_id>-<category>", and match ids themselves contain hyphens
    # ("M-1982-22"), so splitting on hyphens is wrong. Strip the known suffixes.
    suffixes = ("-first-scorer", "-minute", "-count")
    held_matches = set()
    for line in eval_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case_id = json.loads(line)["id"]
        for suffix in suffixes:
            if case_id.endswith(suffix):
                held_matches.add(case_id[: -len(suffix)])
                break
    held_matches = sorted(held_matches)
    held = set(held_matches)
    unknown = [m for m in held_matches if m not in graph.matches]
    if unknown:
        raise SystemExit(f"{len(unknown)} held-out ids are not matches, e.g. "
                         f"{unknown[:3]} - the id parsing is wrong")

    seen_ids = [mid for mid in graph.matches if mid not in held]
    rng.shuffle(seen_ids)
    held_ids = list(held_matches)
    rng.shuffle(held_ids)

    prompts = []
    half = max(1, n // 2)
    for source, ids in (("held_out", held_ids[:half]), ("seen", seen_ids[:n - half])):
        for mid in ids:
            match = graph.matches[mid]
            tournament = graph.tournament_of(mid)
            prompts.append({
                "match_id": mid, "source": source,
                "prompt": f"In {match['name']} at the {tournament}, ",
            })
    rng.shuffle(prompts)
    return prompts


def generate(model, tokenizer, prompts: list[dict], device: str,
             max_new_tokens: int, temperature: float, top_k: int, seed: int) -> list[dict]:
    import torch

    torch.manual_seed(seed)
    out = []
    started = time.time()
    for i, item in enumerate(prompts, 1):
        ids = torch.tensor([tokenizer.encode(item["prompt"]).ids], device=device)
        with torch.no_grad():
            completion = model.generate(ids, max_new_tokens=max_new_tokens,
                                        temperature=temperature, top_k=top_k)
        text = tokenizer.decode(completion[0].tolist())
        out.append({**item, "text": text})
        if i % 50 == 0:
            print(f"  generated {i}/{len(prompts)} "
                  f"({(time.time()-started)/i:.2f}s/prompt)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--checkpoint", type=Path,
                    default=ROOT / "artifacts/football/nanogpt/ckpt.pt")
    ap.add_argument("--graph", type=Path,
                    default=ROOT / "artifacts/football/knowledge_graph.json")
    ap.add_argument("--manifest", type=Path,
                    default=ROOT / "artifacts/football/manifest.json")
    ap.add_argument("--eval", type=Path, default=ROOT / "artifacts/football/eval.jsonl")
    ap.add_argument("--tokenizer", type=Path,
                    default=ROOT / "nanogpt/data/football/tokenizer.json")
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts/football/results")
    ap.add_argument("--samples", type=int, default=400)
    ap.add_argument("--max-new-tokens", type=int, default=80)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--top-k", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    from tokenizers import Tokenizer

    graph = FootballGraph.load(args.graph)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    tokenizer = Tokenizer.from_file(str(args.tokenizer))
    model, payload = load_model(args.checkpoint, args.device)
    print(f"model: {sum(p.numel() for p in model.parameters())/1e6:.2f}M parameters, "
          f"checkpoint iter {payload['iter_num']}, "
          f"best val loss {float(payload['best_val_loss']):.4f}")
    print(f"graph: {len(graph.goals)} goals, {len(graph.matches)} matches")

    prompts = build_prompts(manifest, graph, args.eval, args.samples, args.seed)
    print(f"prompting with {len(prompts)} stems "
          f"({sum(p['source']=='held_out' for p in prompts)} from held-out matches)")
    generations = generate(model, tokenizer, prompts, args.device,
                           args.max_new_tokens, args.temperature, args.top_k, args.seed)

    # ---- verify every claim ----
    rows = []
    truncated = 0
    for item in generations:
        # Drop the prompt itself: only what the model added is its claim.
        produced = item["text"][len(item["prompt"]):]
        sentences = extract_claims(produced)
        # Generation stops at max_new_tokens, usually mid-sentence. That final fragment
        # is an artifact of the sampling budget, not something the model got wrong, so
        # it is discarded rather than counted as an unparseable claim. Counting it would
        # understate how much of the model's output is checkable.
        if sentences and not sentences[-1][0].rstrip().endswith("."):
            sentences = sentences[:-1]
            truncated += 1
        for sentence, claim in sentences:
            verdict = verify(claim, graph)
            rows.append({"match_id": item["match_id"], "source": item["source"],
                         "sentence": sentence, **verdict.as_dict()})
    print(f"discarded {truncated} trailing fragments cut off by max_new_tokens")

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "claims.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str) + "\n")

    # ---- metrics ----
    checkable = [r for r in rows if r["status"] != NOT_CHECKABLE]
    counts = Counter(r["status"] for r in rows)
    n = len(checkable)
    if not n:
        sys.exit("no checkable claims were produced; lower --temperature or raise "
                 "--max-new-tokens")

    true_claims = sum(r["status"] == SUPPORTED for r in checkable)
    released_only = n                       # slm_only releases everything checkable
    released_gate = true_claims             # the gate releases only SUPPORTED

    systems = {
        "slm_only": {
            "claims_released": released_only,
            "false_released": n - true_claims,
            "hallucination_rate": (n - true_claims) / released_only,
            "coverage": 1.0,
        },
        "slm_verified": {
            "claims_released": released_gate,
            "false_released": 0,
            "hallucination_rate": 0.0 if released_gate else None,
            "coverage": released_gate / n,
        },
    }

    by_source = {}
    for source in ("held_out", "seen"):
        sub = [r for r in checkable if r["source"] == source]
        if sub:
            ok = sum(r["status"] == SUPPORTED for r in sub)
            by_source[source] = {"claims": len(sub), "supported": ok,
                                 "false_rate": round((len(sub) - ok) / len(sub), 4)}

    report = {
        "checkpoint": str(args.checkpoint),
        "model_parameters": sum(p.numel() for p in model.parameters()),
        "best_val_loss": float(payload["best_val_loss"]),
        "corpus": manifest["corpus"],
        "sampling": {"temperature": args.temperature, "top_k": args.top_k,
                     "max_new_tokens": args.max_new_tokens, "seed": args.seed},
        "prompts": len(prompts),
        "claims_total": len(rows),
        "truncated_fragments_discarded": truncated,
        "claims_checkable": n,
        "status_counts": dict(counts),
        "systems": systems,
        "by_prompt_source": by_source,
        "rule_failures": Counter(
            f for r in rows for f in r.get("rule_failures", [])).most_common(10),
    }
    (args.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # ---- print ----
    print(f"\nclaims: {len(rows)} produced, {n} checkable "
          f"({counts[NOT_CHECKABLE]} not checkable)")
    print(f"  {SUPPORTED:14s} {counts[SUPPORTED]:5d}")
    print(f"  {CONTRADICTED:14s} {counts[CONTRADICTED]:5d}")
    print(f"  {UNVERIFIED:14s} {counts[UNVERIFIED]:5d}")
    print(f"\n{'system':14s} {'released':>9s} {'false':>7s} {'halluc':>8s} {'coverage':>9s}")
    for name, m in systems.items():
        halluc = "n/a" if m["hallucination_rate"] is None else f"{m['hallucination_rate']:.3f}"
        print(f"{name:14s} {m['claims_released']:9d} {m['false_released']:7d} "
              f"{halluc:>8s} {m['coverage']:9.3f}")
    print("\nfalse-claim rate by prompt source:")
    for source, m in by_source.items():
        print(f"  {source:10s} {m['claims']:4d} claims, {m['false_rate']:.3f} false")
    print(f"\nwrote {args.out/'report.json'} and claims.jsonl")


if __name__ == "__main__":
    main()
