"""Adapter for a merged line-per-row dataset.

The source file has one row per *line* of some underlying file:

    category,source_file,line_number,content

so the real structure is one level down: 70 tabular and textual datasets across
six domains, flattened together. This adapter reconstructs that structure in a
single streaming pass and turns it into knowledge the platform can actually use.

Why not simply embed every row
------------------------------
Retrieving ``143,69,217,5.9,0.58`` as evidence answers nothing and cites nothing
a human can check. A million numeric rows would cost a great deal to embed and
would degrade retrieval for everything else. What is *knowable* about a data
table is its schema, its statistics, and — for small tables — its individual
records. Those are facts with exact provenance, and the reasoning engine and the
verifier can work with them.

So each source file produces:

1. a **dataset card**: prose describing the table, which is genuinely retrievable
   and citable evidence;
2. **schema facts**: row count, column count, column names and inferred types;
3. **statistic facts**: min / max / mean / median for numeric columns, distinct
   counts and modal values for categorical ones — each computed, not extracted,
   and carrying the computation in its provenance;
4. **entity facts** for small tables with an identifier column;
5. **text documents** for genuinely prose sources, sampled rather than exhaustive.

Everything here is streaming and bounded: statistics accumulate incrementally,
categorical counters are capped, and row buffers are dropped once a table proves
too large for row-level facts.
"""
from __future__ import annotations

import csv
import io
import math
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

csv.field_size_limit(min(sys.maxsize, 2_000_000_000))

# Row-level facts are only produced for tables at or below this size. Above it a
# table is described by its statistics instead; emitting five million triples for
# Walmart's weekly sales would bury every other fact in the workspace.
MAX_ROWS_FOR_ENTITY_FACTS = 2_000
# Hard ceiling on triples from any one table, so a wide table cannot dominate the
# workspace's fact set on its own.
MAX_ENTITY_TRIPLES = 8_000
# Cap on distinct values tracked per categorical column.
MAX_CATEGORIES = 200
# Sample kept per numeric column for median and quartiles.
NUMERIC_SAMPLE = 20_000
# Prose records sampled per textual source.
PROSE_SAMPLE = 60

_NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")
_ID_COLUMN = re.compile(r"^(id|.*_id|.*id|key|code|customerid|employee_id)$", re.I)
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slug(value: str) -> str:
    return _SLUG_STRIP.sub("_", (value or "").lower()).strip("_")[:80] or "unnamed"


def sniff_delimiter(header_line: str) -> str:
    counts = {d: header_line.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > 0 else ","


def parse_line(line: str, delimiter: str) -> list[str]:
    try:
        return next(csv.reader(io.StringIO(line), delimiter=delimiter))
    except (csv.Error, StopIteration):
        return line.split(delimiter)


# ---------------------------------------------------------------------------
# Incremental column statistics
# ---------------------------------------------------------------------------

@dataclass
class ColumnStats:
    name: str
    total: int = 0
    empty: int = 0
    numeric_count: int = 0
    numeric_sum: float = 0.0
    minimum: float | None = None
    maximum: float | None = None
    sample: list[float] = field(default_factory=list)
    categories: Counter[str] = field(default_factory=Counter)
    category_overflow: bool = False
    longest: int = 0

    def observe(self, raw: str) -> None:
        value = (raw or "").strip()
        self.total += 1
        if not value:
            self.empty += 1
            return
        self.longest = max(self.longest, len(value))
        if _NUMERIC.match(value):
            number = float(value)
            self.numeric_count += 1
            self.numeric_sum += number
            self.minimum = number if self.minimum is None else min(self.minimum, number)
            self.maximum = number if self.maximum is None else max(self.maximum, number)
            if len(self.sample) < NUMERIC_SAMPLE:
                self.sample.append(number)
        elif not self.category_overflow:
            if len(self.categories) >= MAX_CATEGORIES and value not in self.categories:
                self.category_overflow = True
                self.categories.clear()
            else:
                self.categories[value] += 1

    @property
    def is_numeric(self) -> bool:
        filled = self.total - self.empty
        return filled > 0 and self.numeric_count / filled > 0.9

    @property
    def is_categorical(self) -> bool:
        return (not self.is_numeric and not self.category_overflow
                and 0 < len(self.categories) <= 60)

    @property
    def kind(self) -> str:
        if self.is_numeric:
            return "numeric"
        if self.is_categorical:
            return "categorical"
        return "text"

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name, "kind": self.kind, "values": self.total,
            "missing": self.empty,
        }
        if self.is_numeric and self.numeric_count:
            out["min"] = round(self.minimum, 6) if self.minimum is not None else None
            out["max"] = round(self.maximum, 6) if self.maximum is not None else None
            out["mean"] = round(self.numeric_sum / self.numeric_count, 6)
            if self.sample:
                ordered = sorted(self.sample)
                out["median"] = round(statistics.median(ordered), 6)
                out["sampled"] = len(ordered)
                if len(ordered) >= 4:
                    out["p25"] = round(ordered[len(ordered) // 4], 6)
                    out["p75"] = round(ordered[(3 * len(ordered)) // 4], 6)
        elif self.categories:
            out["distinct"] = len(self.categories)
            out["top"] = [{"value": v, "count": c}
                          for v, c in self.categories.most_common(8)]
        return out


@dataclass
class SourceFile:
    category: str
    source_file: str
    delimiter: str = ","
    header: list[str] = field(default_factory=list)
    columns: dict[str, ColumnStats] = field(default_factory=dict)
    rows: int = 0
    buffered_rows: list[list[str]] | None = field(default_factory=list)
    prose_samples: list[str] = field(default_factory=list)
    malformed: int = 0
    header_seen: bool = False

    @property
    def slug(self) -> str:
        """Derived from the full path, not the stem.

        Two datasets can share a filename - `HR Data/train.csv` and
        `Sales DB/TRAIN.csv` do - and collapsing them onto one subject would
        silently merge their facts into a contradiction.
        """
        path = Path(self.source_file)
        parts = [p for p in (*path.parent.parts, path.stem) if p not in (".", "")]
        return slug("_".join(parts))

    @property
    def is_prose(self) -> bool:
        """A single-column source, or one whose only column is long free text."""
        if len(self.header) <= 1:
            return True
        text_columns = [c for c in self.columns.values() if c.kind == "text"]
        return bool(text_columns) and all(c.longest > 200 for c in text_columns) \
            and len(text_columns) >= max(1, len(self.header) - 1)

    def observe(self, line_number: int, content: str) -> None:
        if not self.header_seen:
            self.delimiter = sniff_delimiter(content)
            self.header = [h.strip().strip('"').lstrip("﻿") or f"column_{i}"
                           for i, h in enumerate(parse_line(content, self.delimiter))]
            self.columns = {name: ColumnStats(name=name) for name in self.header}
            self.header_seen = True
            return

        cells = parse_line(content, self.delimiter)
        if len(cells) != len(self.header):
            self.malformed += 1
            if len(cells) < len(self.header):
                cells = cells + [""] * (len(self.header) - len(cells))
            else:
                cells = cells[:len(self.header)]
        self.rows += 1
        for name, cell in zip(self.header, cells):
            self.columns[name].observe(cell)

        if self.buffered_rows is not None:
            if self.rows > MAX_ROWS_FOR_ENTITY_FACTS:
                self.buffered_rows = None      # too large: statistics only
            else:
                self.buffered_rows.append(cells)
        if len(self.prose_samples) < PROSE_SAMPLE and len(content) > 120:
            self.prose_samples.append(content)

    def identifier_column(self) -> str | None:
        for name in self.header:
            if _ID_COLUMN.match(name.strip()):
                stats = self.columns[name]
                if stats.total and stats.empty / stats.total < 0.05:
                    return name
        return None

    def summary(self) -> dict[str, Any]:
        return {
            "category": self.category, "source_file": self.source_file,
            "slug": self.slug, "rows": self.rows, "columns": len(self.header),
            "malformed_rows": self.malformed, "prose": self.is_prose,
            "column_stats": [c.summary() for c in self.columns.values()],
            "identifier_column": self.identifier_column(),
        }


# ---------------------------------------------------------------------------
# Streaming reader
# ---------------------------------------------------------------------------

def read_merged(path: Path, *, limit: int | None = None,
                categories: set[str] | None = None,
                progress_every: int = 200_000) -> dict[tuple[str, str], SourceFile]:
    """One streaming pass over the merged file, accumulating bounded statistics."""
    files: dict[tuple[str, str], SourceFile] = {}
    seen = 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            seen += 1
            if limit and seen > limit:
                break
            category = (row.get("category") or "").strip()
            if categories and category not in categories:
                continue
            source = (row.get("source_file") or "").strip()
            if not source:
                continue
            key = (category, source)
            entry = files.get(key)
            if entry is None:
                entry = files[key] = SourceFile(category=category, source_file=source)
            try:
                line_number = int(row.get("line_number") or 0)
            except ValueError:
                line_number = entry.rows + 2
            entry.observe(line_number, row.get("content") or "")
            if progress_every and seen % progress_every == 0:
                print(f"  ...{seen:,} rows, {len(files)} source files")
    return files


# ---------------------------------------------------------------------------
# Rendering: dataset cards
# ---------------------------------------------------------------------------

def canonical(value: Any) -> Any:
    """Round a computed statistic to the precision the dataset card prints.

    The card is the evidence and the fact is the claim; if the fact keeps
    34.803915 while the card prints 34.8039 they can never be matched, and the
    system abstains on a value it computed itself.
    """
    if isinstance(value, float):
        if value.is_integer():
            return value
        return round(value, 2 if abs(value) >= 1000 else 4)
    return value


def _fmt(value: Any) -> str:
    """Readable fixed-point. Scientific notation would be unmatchable.

    A card saying "the mean is 3.92e+04" cannot be checked against an answer
    saying "39,200", and the grounding check compares numbers literally.
    """
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e15:
            return f"{int(value):,}"
        if abs(value) >= 1000:
            return f"{value:,.2f}"
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def dataset_card(source: SourceFile) -> str:
    """A prose description of one table.

    This is the retrievable, citable artifact. It states what the table is, what
    its columns are, and what its computed statistics say — so a question about
    the data can be answered from evidence a person can verify by opening the
    original file.
    """
    name = Path(source.source_file).name
    lines = [
        f"# Dataset: {name}",
        "",
        f"Domain: {source.category}. Source file: `{source.source_file}`.",
        f"The {name} table has {source.rows:,} data rows and "
        f"{len(source.header)} columns.",
        "",
        "## Columns",
        "",
    ]
    for stats in source.columns.values():
        summary = stats.summary()
        if summary["kind"] == "numeric" and "mean" in summary:
            lines.append(
                f"- `{stats.name}` is numeric. The minimum {stats.name} is "
                f"{_fmt(summary['min'])} and the maximum {stats.name} is "
                f"{_fmt(summary['max'])}. The mean {stats.name} is "
                f"{_fmt(summary['mean'])}"
                + (f" and the median {stats.name} is {_fmt(summary['median'])}"
                   if "median" in summary else "")
                + f". {summary['missing']:,} values are missing."
            )
        elif summary["kind"] == "categorical":
            top = ", ".join(f"{item['value']} ({item['count']:,})"
                            for item in summary.get("top", [])[:5])
            lines.append(
                f"- `{stats.name}` is categorical with {summary['distinct']} distinct "
                f"values. The most common are: {top}."
            )
        else:
            lines.append(
                f"- `{stats.name}` is free text, up to {stats.longest:,} characters, "
                f"with {summary['missing']:,} missing values."
            )

    if source.malformed:
        lines += ["", "## Data quality", "",
                  f"{source.malformed:,} rows did not match the header's column count "
                  f"and were padded or truncated during ingestion."]

    identifier = source.identifier_column()
    if identifier:
        lines += ["", "## Identifier", "",
                  f"Rows are identified by `{identifier}`."]

    lines += [
        "", "## Provenance", "",
        f"These figures were computed during ingestion from all {source.rows:,} rows "
        f"of `{source.source_file}`, not extracted from prose. Percentile and median "
        f"figures are computed from a sample of up to {NUMERIC_SAMPLE:,} values.",
    ]
    return "\n".join(lines)


def prose_document(source: SourceFile) -> str:
    """A sampled text document for a genuinely textual source."""
    name = Path(source.source_file).name
    lines = [
        f"# Text corpus: {name}",
        "",
        f"Domain: {source.category}. Source file: `{source.source_file}`.",
        f"This source contains {source.rows:,} text records. The excerpts below are "
        f"a sample of {min(len(source.prose_samples), PROSE_SAMPLE)} of them, included "
        f"so the corpus is searchable; they are not the complete set.",
        "",
    ]
    for index, sample in enumerate(source.prose_samples[:PROSE_SAMPLE], start=1):
        cleaned = re.sub(r"<[^>]{1,20}>", " ", sample)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        lines.append(f"## Record {index}")
        lines.append("")
        lines.append(cleaned[:1200])
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rendering: facts
# ---------------------------------------------------------------------------

@dataclass
class TripleDraft:
    subject: str
    predicate: str
    value: Any
    unit: str = ""
    evidence: str = ""
    extractor: str = "merged_dataset"


def schema_and_statistic_facts(source: SourceFile) -> list[TripleDraft]:
    """Computed facts about a table. Every one is arithmetic over the real rows."""
    subject = f"dataset:{source.slug}"
    name = Path(source.source_file).name
    out: list[TripleDraft] = [
        TripleDraft(subject, "row_count", float(source.rows), "rows",
                    f"Counted {source.rows} data rows in {name} during ingestion."),
        TripleDraft(subject, "column_count", float(len(source.header)), "columns",
                    f"{name} has {len(source.header)} columns: "
                    f"{', '.join(source.header[:20])}."),
        TripleDraft(subject, "domain", source.category, "",
                    f"{name} belongs to the {source.category} domain."),
    ]

    for stats in source.columns.values():
        summary = stats.summary()
        column = slug(stats.name)
        if summary["kind"] == "numeric" and "mean" in summary:
            base = f"Computed over {stats.numeric_count} values of {stats.name} in {name}."
            out += [
                TripleDraft(subject, f"{column}_min", canonical(summary["min"]), "", base),
                TripleDraft(subject, f"{column}_max", canonical(summary["max"]), "", base),
                TripleDraft(subject, f"{column}_mean", canonical(summary["mean"]), "",
                            base),
            ]
            if "median" in summary:
                out.append(TripleDraft(subject, f"{column}_median",
                                       canonical(summary["median"]), "", base))
        elif summary["kind"] == "categorical":
            out.append(TripleDraft(
                subject, f"{column}_distinct_count", float(summary["distinct"]), "",
                f"{stats.name} has {summary['distinct']} distinct values in {name}."))
            top = summary.get("top") or []
            if top:
                out.append(TripleDraft(
                    subject, f"{column}_most_common", top[0]["value"], "",
                    f"The most common {stats.name} in {name} is "
                    f"{top[0]['value']} with {top[0]['count']} rows."))
        if stats.empty and summary["kind"] in ("numeric", "categorical"):
            out.append(TripleDraft(
                subject, f"{column}_missing_count", float(stats.empty), "",
                f"{stats.name} is missing in {stats.empty} rows of {name}."))
    return out


def entity_facts(source: SourceFile) -> list[TripleDraft]:
    """Row-level triples, only for tables small enough to be worth materialising."""
    if source.buffered_rows is None or not source.buffered_rows:
        return []
    identifier = source.identifier_column()
    if identifier is None:
        return []
    index = source.header.index(identifier)

    # An identifier column that repeats is not an identifier. Emitting triples
    # keyed on it produces many facts sharing a subject and predicate, which the
    # contradiction detector then reports as sources disagreeing - when really
    # the column was a group label all along.
    keys = [(row[index] or "").strip() for row in source.buffered_rows]
    populated = [k for k in keys if k]
    if not populated or len(set(populated)) < len(populated) * 0.98:
        return []

    prefix = source.slug
    name = Path(source.source_file).name
    out: list[TripleDraft] = []
    for cells in source.buffered_rows:
        if len(out) >= MAX_ENTITY_TRIPLES:
            break
        key = (cells[index] or "").strip()
        if not key:
            continue
        subject = f"{prefix}:{key}"
        for column, cell in zip(source.header, cells):
            value = (cell or "").strip()
            if not value or column == identifier:
                continue
            out.append(TripleDraft(
                subject, slug(column), value, "",
                f"Row {key} of {name}: {column} = {value}.", "record_mapping"))
    return out


def iter_source_files(files: dict[tuple[str, str], SourceFile]) -> Iterator[SourceFile]:
    yield from sorted(files.values(), key=lambda f: (-f.rows, f.source_file))
