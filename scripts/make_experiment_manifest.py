"""Build the canonical experiment manifest from the artifacts on disk.

    python scripts/make_experiment_manifest.py

Writes `docs/EXPERIMENT_MANIFEST.md` and `.json`.

Every row is discovered by walking `eval_reports/` and `artifacts/`, not typed in. An
experiment appears here only if its per-question or per-claim records exist; one whose
records are missing is listed as unresolved rather than described from memory.

Reproducibility status is decided by recomputation, not by assertion: for each research
report the published metrics are recomputed from the stored rows and compared.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "eval_reports/research"
NSBENCH = ROOT / "eval_reports"
FOOTBALL = ROOT / "artifacts/football"
ANSWERABLE = ("decision", "value", "result")


def sha256(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def rows(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def recompute(records: list[dict]) -> dict:
    n = len(records)
    answerable = [r for r in records if r.get("gold_kind") in ANSWERABLE]
    definite = [r for r in answerable if r.get("definite")]
    return {
        "accuracy": sum(bool(r.get("correct")) for r in records) / n if n else None,
        "hallucination_rate": (sum(bool(r.get("hallucinated")) for r in records) / n
                               if n else None),
        "coverage": len(definite) / len(answerable) if answerable else None,
        "selective_accuracy": (sum(bool(r.get("correct")) for r in definite)
                               / len(definite) if definite else None),
    }


def research_experiments() -> list[dict]:
    out = []
    if not RESEARCH.exists():
        return out
    for tag_dir in sorted(RESEARCH.iterdir()):
        if not tag_dir.is_dir():
            continue
        for report in sorted(tag_dir.glob("*.report.json")):
            split = report.stem.split(".")[0]
            payload = json.loads(report.read_text(encoding="utf-8"))
            published = payload.get("systems", {})
            checked = mismatched = 0
            systems = []
            for jsonl in sorted(tag_dir.glob(f"{split}.*.jsonl")):
                system = jsonl.stem.split(".", 1)[1]
                if system == "report":
                    continue
                records = rows(jsonl)
                systems.append({"system": system, "cases": len(records),
                                "records": str(jsonl.relative_to(ROOT)),
                                "sha256": sha256(jsonl)})
                pub = published.get(system, {}).get("overall", {})
                if not pub:
                    continue
                got = recompute(records)
                for key, mine in got.items():
                    entry = pub.get(key)
                    theirs = entry.get("value") if isinstance(entry, dict) else entry
                    if mine is None or theirs is None:
                        continue
                    checked += 1
                    if abs(mine - theirs) >= 5e-4:
                        mismatched += 1
            out.append({
                "id": f"research/{tag_dir.name}/{split}",
                "kind": "enterprise benchmark",
                "tag": tag_dir.name,
                "split": split,
                "cases": payload.get("cases"),
                "systems": systems,
                "report": str(report.relative_to(ROOT)),
                "metrics_checked": checked,
                "metrics_mismatched": mismatched,
                "reproducible": (checked > 0 and mismatched == 0),
            })
    # Tag directories with records but no report: real data, no published aggregate.
    for tag_dir in sorted(RESEARCH.iterdir()):
        if not tag_dir.is_dir():
            continue
        for jsonl in sorted(tag_dir.glob("*.jsonl")):
            split = jsonl.stem.split(".")[0]
            if (tag_dir / f"{split}.report.json").exists():
                continue
            if any(e["id"] == f"research/{tag_dir.name}/{split}+raw" for e in out):
                continue
            out.append({
                "id": f"research/{tag_dir.name}/{split}+raw",
                "kind": "enterprise benchmark (records only)",
                "tag": tag_dir.name, "split": split,
                "cases": len(rows(jsonl)),
                "systems": [], "report": None,
                "metrics_checked": 0, "metrics_mismatched": 0,
                "reproducible": None,
                "note": "per-question records present, no aggregate report written",
            })
            break
    return out


def football_experiment() -> dict | None:
    report = FOOTBALL / "results/report.json"
    if not report.exists():
        return None
    payload = json.loads(report.read_text(encoding="utf-8"))
    manifest = FOOTBALL / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    claims = FOOTBALL / "results/claims.jsonl"
    return {
        "id": "football/claim-gating",
        "kind": "from-scratch model, claim-level gating",
        "model": {"parameters": payload.get("model_parameters"),
                  "best_val_loss": payload.get("best_val_loss"),
                  "checkpoint": "artifacts/football/nanogpt/ckpt.pt"},
        "corpus": payload.get("corpus"),
        "source": data.get("source"),
        "split": data.get("split"),
        "sampling": payload.get("sampling"),
        "claims_total": payload.get("claims_total"),
        "claims_checkable": payload.get("claims_checkable"),
        "records": str(claims.relative_to(ROOT)) if claims.exists() else None,
        "sha256": sha256(claims) if claims.exists() else None,
        "reproducible": claims.exists(),
        "caveat": ("the gated hallucination rate is zero by construction: only "
                   "graph-supported claims are released, so this is not independent "
                   "evidence of verifier precision"),
    }


def main() -> None:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    experiments = research_experiments()
    football = football_experiment()
    if football:
        experiments.append(football)

    payload = {"generated": generated, "git_commit": git_commit(),
               "experiments": experiments}
    (ROOT / "docs/EXPERIMENT_MANIFEST.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# Experiment manifest",
        "",
        f"Generated {generated} from the artifacts on disk by "
        "`scripts/make_experiment_manifest.py`. Commit `{}`.".format(payload["git_commit"]),
        "",
        "An experiment appears here only when its per-question or per-claim records "
        "exist. **Reproducible** means the published aggregate was recomputed from "
        "those records and matched, not that the run was repeated end to end.",
        "",
        "| Experiment | Kind | Cases | Systems | Metrics checked | Mismatched | Reproducible |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for e in experiments:
        if e["id"].startswith("football"):
            lines.append(f"| `{e['id']}` | {e['kind']} | "
                         f"{e.get('claims_checkable', '-')} claims | 2 | - | - | "
                         f"{'records present' if e['reproducible'] else 'NO RECORDS'} |")
            continue
        status = ("yes" if e["reproducible"] else
                  "unresolved" if e["reproducible"] is None else "NO")
        lines.append(f"| `{e['id']}` | {e['kind']} | {e.get('cases') or '-'} | "
                     f"{len(e['systems'])} | {e['metrics_checked']} | "
                     f"{e['metrics_mismatched']} | {status} |")

    lines += ["", "## Per-system records", ""]
    for e in experiments:
        if not e.get("systems"):
            continue
        lines.append(f"### `{e['id']}`")
        lines.append("")
        lines.append("| System | Cases | Records | SHA-256 (16) |")
        lines.append("|---|---:|---|---|")
        for s in e["systems"]:
            lines.append(f"| {s['system']} | {s['cases']} | `{s['records']}` | "
                         f"`{s['sha256']}` |")
        lines.append("")

    if football:
        lines += [
            "## Secondary study: from-scratch model",
            "",
            f"- Model: {football['model']['parameters']:,} parameters, best validation "
            f"loss {football['model']['best_val_loss']:.4f}",
            f"- Corpus: {football['corpus']['tokens']['train']:,} training tokens, "
            f"vocabulary {football['corpus']['vocab_size']}",
            f"- Claims: {football['claims_total']:,} produced, "
            f"{football['claims_checkable']:,} checkable",
            f"- Records: `{football['records']}`",
            "",
            f"**Caveat.** {football['caveat']}.",
            "",
        ]

    lines += [
        "## Unresolved",
        "",
        "- Independent verification quality. The verifier is correct by construction "
        "with respect to its own graph; precision and recall against externally "
        "authored labels are unmeasured.",
        "- Question relevance. No relevance gate is implemented, so a supported answer "
        "to a different question is not detected.",
        "- Reference metadata. Bibliographic entries are formatted from known venues; "
        "DOIs and page numbers have not been checked against publisher records.",
        "",
    ]
    (ROOT / "docs/EXPERIMENT_MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(experiments)} experiments recorded")
    for e in experiments:
        if e.get("metrics_checked"):
            print(f"  {e['id']}: {e['metrics_checked']} metrics, "
                  f"{e['metrics_mismatched']} mismatched")
    print("wrote docs/EXPERIMENT_MANIFEST.md and .json")


if __name__ == "__main__":
    main()
