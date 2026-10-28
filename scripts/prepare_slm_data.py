"""Build the from-scratch model's training corpus, tokenizer and token streams.

    python scripts/prepare_slm_data.py --corpus wikitext103 --vocab-size 8192 \
        --max-documents 200000 --out artifacts/slm/data

Writes into `--out`:
    tokenizer.json      BPE tokenizer trained on this corpus only
    train.bin           uint16 token stream
    validation.bin
    test.bin
    manifest.json       counts, hashes, licence, leakage-check result

The manifest is the record a result is traced back to. If the leakage check finds a
benchmark question inside the corpus, preparation fails rather than warns: training on
the evaluation set would invalidate every number downstream.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.slm import data as D  # noqa: E402


def iter_corpus(spec: D.CorpusSpec, max_documents: int | None):
    """Stream the declared corpus, cleaning as we go."""
    from datasets import load_dataset

    print(f"loading {spec.hf_path} ({spec.hf_name or 'default'}) split={spec.split}")
    dataset = load_dataset(spec.hf_path, spec.hf_name, split=spec.split,
                           streaming=True)
    kept = 0
    for row in dataset:
        cleaned = D.clean_document(row[spec.text_column])
        if cleaned is None:
            continue
        yield cleaned
        kept += 1
        if max_documents and kept >= max_documents:
            break


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", default="wikitext103", choices=sorted(D.CORPORA))
    ap.add_argument("--vocab-size", type=int, default=8192)
    ap.add_argument("--max-documents", type=int, default=None,
                    help="cap the corpus (for a pilot run); omit for everything")
    ap.add_argument("--out", type=Path, default=Path("artifacts/slm/data"))
    ap.add_argument("--benchmark-dir", type=Path,
                    default=BACKEND / "data/meridian/benchmark",
                    help="PRAXIS benchmark splits, scanned for leakage")
    ap.add_argument("--no-near-dedup", action="store_true",
                    help="skip MinHash near-duplicate removal (faster, less clean)")
    ap.add_argument("--allow-leakage", action="store_true",
                    help="record leakage hits but do not fail. Never use for a "
                         "reported run")
    args = ap.parse_args()

    if args.vocab_size > D.MAX_VOCAB_FOR_DTYPE:
        ap.error(f"--vocab-size must be <= {D.MAX_VOCAB_FOR_DTYPE} for uint16 streams")

    spec = D.CORPORA[args.corpus]
    args.out.mkdir(parents=True, exist_ok=True)
    started = time.time()

    # ---- pass 1: clean, dedup, split -------------------------------------
    print("pass 1: cleaning, deduplicating and splitting")
    near = None if args.no_near_dedup else D.NearDuplicateFilter()
    seen_exact: set[str] = set()
    splits: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    stats = {"read": 0, "exact_duplicates": 0, "near_duplicates": 0, "bytes": 0}

    for document in iter_corpus(spec, args.max_documents):
        stats["read"] += 1
        digest = D._digest(document)
        if digest in seen_exact:
            stats["exact_duplicates"] += 1
            continue
        seen_exact.add(digest)
        if near is not None and near.is_duplicate(document):
            stats["near_duplicates"] += 1
            continue
        stats["bytes"] += len(document.encode("utf-8"))
        splits[D.assign_split(document)].append(document)
        if stats["read"] % 25000 == 0:
            print(f"  {stats['read']:,} read, "
                  f"{sum(len(v) for v in splits.values()):,} kept")

    for name, docs in splits.items():
        print(f"  {name}: {len(docs):,} documents")
    if not splits["train"]:
        sys.exit("no training documents survived cleaning - check the corpus spec")
    for name in ("validation", "test"):
        if not splits[name]:
            sys.exit(f"the {name} split is empty; raise --max-documents")

    # ---- leakage check ---------------------------------------------------
    print("checking for evaluation leakage")
    probes = D.collect_benchmark_strings(args.benchmark_dir)
    print(f"  {len(probes)} benchmark strings collected from {args.benchmark_dir}")
    hits = D.check_leakage(
        (d for docs in splits.values() for d in docs), probes) if probes else []
    if hits:
        print(f"  LEAKAGE: {len(hits)} benchmark strings found in the corpus")
        for hit in hits[:10]:
            print(f"    - {hit['probe'][:100]!r}")
        if not args.allow_leakage:
            sys.exit("aborting: training on evaluation data would invalidate the "
                     "results. Re-run with --allow-leakage only for debugging.")
    else:
        print("  clean: no benchmark question or answer appears in the corpus")

    # ---- tokenizer -------------------------------------------------------
    print(f"training a byte-level BPE tokenizer, vocab_size={args.vocab_size}")
    # Trained on the training split only: fitting the vocabulary on validation or
    # test text is a subtle form of leakage.
    tokenizer_path = D.train_tokenizer(iter(splits["train"]), args.vocab_size, args.out)
    tokenizer = D.load_tokenizer(tokenizer_path)
    eos_id = tokenizer.token_to_id("<|endoftext|>")
    print(f"  vocab {tokenizer.get_vocab_size()}, eos id {eos_id}")

    # ---- pass 2: encode and pack ----------------------------------------
    token_counts: dict[str, int] = {}
    for name, docs in splits.items():
        print(f"encoding {name} ({len(docs):,} documents)")
        encoded = (enc.ids for enc in tokenizer.encode_batch(docs))
        count = D.pack_tokens(encoded, args.out / f"{name}.bin", eos_id)
        token_counts[name] = count
        print(f"  {count:,} tokens -> {name}.bin")

    # ---- manifest --------------------------------------------------------
    total_tokens = sum(token_counts.values())
    manifest = {
        "corpus": {
            "key": spec.key, "hf_path": spec.hf_path, "hf_name": spec.hf_name,
            "split": spec.split, "licence": spec.licence,
            "source_url": spec.source_url, "description": spec.description,
        },
        "preparation": {
            "documents_read": stats["read"],
            "exact_duplicates_removed": stats["exact_duplicates"],
            "near_duplicates_removed": stats["near_duplicates"],
            "near_dedup_enabled": not args.no_near_dedup,
            "documents_kept": sum(len(v) for v in splits.values()),
            "corpus_bytes": stats["bytes"],
            "max_documents": args.max_documents,
            "split_method": "sha256(document) bucketed - deterministic across runs",
            "split_fractions": {"validation": D.VAL_FRACTION, "test": D.TEST_FRACTION},
            "minutes": round((time.time() - started) / 60, 2),
        },
        "tokenizer": {
            "type": "byte-level BPE, trained on this corpus' train split only",
            "vocab_size": tokenizer.get_vocab_size(),
            "special_tokens": D.SPECIAL_TOKENS,
            "sha256": D.sha256_file(tokenizer_path),
        },
        "documents": {k: len(v) for k, v in splits.items()},
        "tokens": token_counts,
        "total_tokens": total_tokens,
        "bytes_per_token": (round(stats["bytes"] / total_tokens, 3)
                            if total_tokens else None),
        "leakage_check": {
            "benchmark_dir": str(args.benchmark_dir),
            "probes": len(probes),
            "hits": len(hits),
            "hit_samples": [h["probe"] for h in hits[:20]],
            "passed": not hits,
        },
        "artifacts": {
            name: {"file": f"{name}.bin",
                   "sha256": D.sha256_file(args.out / f"{name}.bin"),
                   "tokens": token_counts[name]}
            for name in splits
        },
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                            encoding="utf-8")
    print(f"\nwrote {args.out / 'manifest.json'}")
    print(f"total {total_tokens:,} tokens from {stats['bytes']/1e6:.1f} MB of text")


if __name__ == "__main__":
    main()
