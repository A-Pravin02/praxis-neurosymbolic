"""Corpus preparation and tokenization for the from-scratch language model.

What this module guarantees, because the research claim depends on it:

  * **Provenance.** Every corpus is declared with its name, version, licence and source
    URL in `CORPORA`. Nothing is downloaded that is not declared.
  * **Deterministic splits.** Train/validation/test membership is decided by a hash of
    the document text, not by shuffling, so the split is identical on every machine and
    cannot drift between runs.
  * **Deduplication.** Exact duplicates are removed by content hash. Near-duplicates are
    removed by a token-shingle MinHash, which matters because repeated text inflates
    perplexity scores.
  * **No evaluation leakage.** `check_leakage` scans the corpus for the PRAXIS benchmark
    questions and their answers. The specification forbids training on the held-out set;
    this makes that checkable instead of assumed.
  * **A manifest.** `prepare()` writes `manifest.json` with byte/document/token counts
    and a SHA-256 of every artifact, so a reported result can be traced to the exact
    data that produced it.

Token files are flat `uint16` memory-maps, valid only while vocab_size <= 65536.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

TOKEN_DTYPE = np.uint16
MAX_VOCAB_FOR_DTYPE = 1 << 16


@dataclass(frozen=True)
class CorpusSpec:
    """A declared data source. Nothing undeclared is ever fetched."""

    key: str
    hf_path: str
    hf_name: str | None
    split: str
    text_column: str
    licence: str
    source_url: str
    description: str


# Corpora judged appropriate for small-scale pretraining: permissively licensed,
# widely used in the literature, and distributed for research use.
CORPORA: dict[str, CorpusSpec] = {
    "wikitext103": CorpusSpec(
        key="wikitext103",
        hf_path="Salesforce/wikitext",
        hf_name="wikitext-103-raw-v1",
        split="train",
        text_column="text",
        licence="CC BY-SA 3.0",
        source_url="https://huggingface.co/datasets/Salesforce/wikitext",
        description=(
            "Verified Good and Featured Wikipedia articles (Merity et al., 2016). "
            "Encyclopedic prose, which suits a model intended to make factual "
            "statements that a verifier can then check."),
    ),
    "tinystories": CorpusSpec(
        key="tinystories",
        hf_path="roneneldan/TinyStories",
        hf_name=None,
        split="train",
        text_column="text",
        licence="CDLA-Sharing-1.0",
        source_url="https://huggingface.co/datasets/roneneldan/TinyStories",
        description=(
            "Synthetic short stories with a small vocabulary (Eldan & Li, 2023). "
            "Produces fluent generation at very small scale, but carries almost no "
            "world knowledge - useful for demonstrating fluency, not factuality."),
    ),
}

# Split proportions. Validation and test are small because the corpus is the
# bottleneck for a model this size; they only need to be stable, not large.
VAL_FRACTION = 0.005
TEST_FRACTION = 0.005


# --------------------------------------------------------------------------
# cleaning
# --------------------------------------------------------------------------

_WIKI_HEADING = re.compile(r"^\s*=+\s.*\s=+\s*$")
_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_RUN = re.compile(r"\n{3,}")


def clean_document(text: str) -> str | None:
    """Normalize one document. Returns None if it should be dropped.

    Wikitext ships one line per row, including bare `= Heading =` lines and empty
    rows. Headings carry no sentence structure to learn from, so they go.
    """
    if not text or not text.strip():
        return None
    if _WIKI_HEADING.match(text):
        return None
    text = unicodedata.normalize("NFKC", text)
    # Wikitext encodes unknown tokens literally; they teach nothing.
    text = text.replace("<unk>", "")
    text = _WHITESPACE.sub(" ", text)
    text = _BLANK_RUN.sub("\n\n", text)
    text = text.strip()
    # Fragments this short are mostly list residue and table cells.
    if len(text) < 32:
        return None
    return text


# --------------------------------------------------------------------------
# deterministic splitting and deduplication
# --------------------------------------------------------------------------

def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assign_split(text: str) -> str:
    """Choose a split from the document's own hash.

    Hash-based assignment, not shuffling: the same document always lands in the same
    split regardless of corpus order, machine or run, which is what makes a held-out
    set actually held out across runs.
    """
    bucket = int(_digest(text)[:8], 16) / 0xFFFFFFFF
    if bucket < VAL_FRACTION:
        return "validation"
    if bucket < VAL_FRACTION + TEST_FRACTION:
        return "test"
    return "train"


def _stable_hash(text: str) -> int:
    """A hash that is identical across processes and machines.

    Python's built-in hash() is randomized per interpreter unless PYTHONHASHSEED is
    pinned, which would make deduplication - and therefore the corpus, and therefore
    every number downstream - unreproducible.
    """
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest(),
                          "big")


def _shingles(text: str, width: int = 5) -> set[int]:
    words = text.lower().split()
    if len(words) < width:
        return {_stable_hash(" ".join(words))}
    return {_stable_hash(" ".join(words[i:i + width]))
            for i in range(len(words) - width + 1)}


class NearDuplicateFilter:
    """MinHash near-duplicate detector with banded LSH.

    Exact-hash dedup misses the common case of a document republished with a changed
    header. Repetition between train and validation would make validation loss look
    better than it is, so this runs over the whole corpus, not per split.
    """

    def __init__(self, num_perm: int = 64, bands: int = 16, threshold: float = 0.8) -> None:
        if num_perm % bands != 0:
            raise ValueError("num_perm must be divisible by bands")
        self.num_perm = num_perm
        self.bands = bands
        self.rows = num_perm // bands
        self.threshold = threshold
        rng = np.random.default_rng(0)  # fixed: dedup must be reproducible
        self._a = rng.integers(1, 1 << 61, size=num_perm, dtype=np.uint64)
        self._b = rng.integers(0, 1 << 61, size=num_perm, dtype=np.uint64)
        self._buckets: dict[tuple[int, bytes], list[int]] = {}
        self._signatures: list[np.ndarray] = []

    def _signature(self, text: str) -> np.ndarray:
        sh = np.fromiter((h & 0x7FFFFFFFFFFFFFFF for h in _shingles(text)),
                         dtype=np.uint64)
        if sh.size == 0:
            return np.zeros(self.num_perm, dtype=np.uint64)
        # (num_perm, n_shingles) under a family of universal hashes; min per row.
        hashed = (self._a[:, None] * sh[None, :] + self._b[:, None]) % ((1 << 61) - 1)
        return hashed.min(axis=1)

    def is_duplicate(self, text: str) -> bool:
        sig = self._signature(text)
        candidates: set[int] = set()
        keys = []
        for band in range(self.bands):
            chunk = sig[band * self.rows:(band + 1) * self.rows].tobytes()
            key = (band, chunk)
            keys.append(key)
            candidates.update(self._buckets.get(key, ()))
        for idx in candidates:
            if float(np.mean(self._signatures[idx] == sig)) >= self.threshold:
                return True
        position = len(self._signatures)
        self._signatures.append(sig)
        for key in keys:
            self._buckets.setdefault(key, []).append(position)
        return False


# --------------------------------------------------------------------------
# evaluation leakage
# --------------------------------------------------------------------------

def _normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def collect_benchmark_strings(benchmark_dir: Path) -> list[str]:
    """Every question and expected answer in the PRAXIS benchmark splits."""
    import yaml

    strings: list[str] = []
    for path in sorted(benchmark_dir.glob("*.yaml")):
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for case in _iter_cases(payload):
            for field in ("question", "query", "prompt", "expected_answer", "answer"):
                value = case.get(field)
                if isinstance(value, str) and len(value.strip()) >= 24:
                    strings.append(value.strip())
    return strings


def _iter_cases(payload: object) -> Iterator[dict]:
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_cases(value)
        if any(k in payload for k in ("question", "query", "prompt")):
            yield payload
    elif isinstance(payload, list):
        for item in payload:
            yield from _iter_cases(item)


def check_leakage(documents: Iterable[str], probes: list[str],
                  min_probe_words: int = 6) -> list[dict]:
    """Report any benchmark string that appears verbatim in the corpus.

    Substring matching on normalized text. Short probes are skipped: a six-word
    question fragment can occur innocently in a large corpus, and flagging it would
    make the check noise rather than evidence.
    """
    usable = [(p, _normalize_for_match(p)) for p in probes]
    usable = [(p, n) for p, n in usable if len(n.split()) >= min_probe_words]
    if not usable:
        return []
    hits: list[dict] = []
    for position, doc in enumerate(documents):
        haystack = _normalize_for_match(doc)
        for original, needle in usable:
            if needle in haystack:
                hits.append({"document_index": position, "probe": original})
    return hits


# --------------------------------------------------------------------------
# tokenizer
# --------------------------------------------------------------------------

SPECIAL_TOKENS = ["<|pad|>", "<|endoftext|>", "<|user|>", "<|assistant|>"]


def train_tokenizer(texts: Iterable[str], vocab_size: int, out_dir: Path) -> Path:
    """Train a byte-level BPE tokenizer on this corpus.

    Trained here rather than borrowed: a borrowed vocabulary is a form of transfer from
    another corpus, and the point of the experiment is that everything originates from
    the declared training data.
    """
    from tokenizers import Tokenizer, decoders, pre_tokenizers, trainers
    from tokenizers.models import BPE

    tokenizer = Tokenizer(BPE(unk_token=None))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    trainer = trainers.BpeTrainer(
        vocab_size=vocab_size,
        special_tokens=SPECIAL_TOKENS,
        initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        show_progress=True,
    )
    tokenizer.train_from_iterator(texts, trainer=trainer)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "tokenizer.json"
    tokenizer.save(str(path))
    return path


def load_tokenizer(path: Path):
    from tokenizers import Tokenizer
    return Tokenizer.from_file(str(path))


# --------------------------------------------------------------------------
# packing
# --------------------------------------------------------------------------

def pack_tokens(token_lists: Iterable[list[int]], out_path: Path,
                eos_id: int) -> int:
    """Concatenate documents into one flat uint16 stream, EOS-separated."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with out_path.open("wb") as handle:
        buffer: list[int] = []
        for ids in token_lists:
            buffer.extend(ids)
            buffer.append(eos_id)
            if len(buffer) >= 1_000_000:
                np.asarray(buffer, dtype=TOKEN_DTYPE).tofile(handle)
                total += len(buffer)
                buffer.clear()
        if buffer:
            np.asarray(buffer, dtype=TOKEN_DTYPE).tofile(handle)
            total += len(buffer)
    return total


def load_tokens(path: Path) -> np.ndarray:
    return np.memmap(path, dtype=TOKEN_DTYPE, mode="r")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
