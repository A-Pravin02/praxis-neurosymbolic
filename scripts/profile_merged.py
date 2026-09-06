#!/usr/bin/env python
"""Profile a merged line-per-row dataset before deciding how to ingest it.

The file has one row per *line* of some underlying source file:

    category,source_file,line_number,content

so the interesting structure is one level down. This walks it once, streaming,
and reports what is actually in there: categories, source files, how big each is,
whether a file looks tabular or like prose, and what its header row says.

Writes nothing. Reads once.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sys

csv.field_size_limit(min(sys.maxsize, 2_000_000_000))

_NUMERIC = re.compile(r"^-?\d+(?:\.\d+)?$")


def looks_tabular(sample_lines: list[str]) -> tuple[bool, int]:
    """A file is tabular when its lines have a consistent comma/tab arity > 1."""
    counts = [line.count(",") for line in sample_lines if line.strip()]
    if not counts:
        return False, 0
    common, frequency = collections.Counter(counts).most_common(1)[0]
    consistent = frequency / len(counts) > 0.7
    return (consistent and common >= 1), common + 1


def numeric_ratio(sample_lines: list[str]) -> float:
    cells: list[str] = []
    for line in sample_lines[:40]:
        cells.extend(c.strip().strip('"') for c in line.split(","))
    if not cells:
        return 0.0
    return sum(1 for c in cells if _NUMERIC.match(c)) / len(cells)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("--sample", type=int, default=12,
                        help="lines to keep per source file for shape detection")
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args()

    per_file: dict[tuple[str, str], dict] = {}
    categories: collections.Counter[str] = collections.Counter()
    total_rows = 0
    total_chars = 0

    with args.path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.DictReader(handle)
        print(f"columns: {reader.fieldnames}")
        for row in reader:
            total_rows += 1
            category = (row.get("category") or "").strip()
            source = (row.get("source_file") or "").strip()
            content = row.get("content") or ""
            total_chars += len(content)
            categories[category] += 1

            key = (category, source)
            entry = per_file.get(key)
            if entry is None:
                entry = per_file[key] = {
                    "category": category, "source_file": source, "lines": 0,
                    "chars": 0, "sample": [], "first_line": content[:400],
                }
            entry["lines"] += 1
            entry["chars"] += len(content)
            if len(entry["sample"]) < args.sample:
                entry["sample"].append(content)

    files = []
    for entry in per_file.values():
        tabular, columns = looks_tabular(entry["sample"])
        entry["tabular"] = tabular
        entry["columns"] = columns if tabular else 0
        entry["numeric_ratio"] = round(numeric_ratio(entry["sample"]), 3)
        entry["mean_line_chars"] = round(entry["chars"] / max(1, entry["lines"]), 1)
        entry["header"] = entry["first_line"] if tabular else ""
        files.append(entry)

    files.sort(key=lambda e: -e["lines"])

    report = {
        "total_rows": total_rows,
        "total_content_chars": total_chars,
        "categories": categories.most_common(),
        "source_files": len(files),
        "tabular_files": sum(1 for f in files if f["tabular"]),
        "prose_files": sum(1 for f in files if not f["tabular"]),
        "files": [
            {k: v for k, v in f.items() if k not in ("sample", "first_line", "chars")}
            for f in files
        ],
    }

    print(f"\nrows: {total_rows:,}   content: {total_chars / 1e6:.1f} M chars")
    print(f"source files: {len(files)}   "
          f"tabular: {report['tabular_files']}   prose: {report['prose_files']}")

    print("\ncategories:")
    for category, count in categories.most_common():
        share = 100 * count / max(1, total_rows)
        print(f"  {category or '(blank)':<28} {count:>10,}  ({share:4.1f}%)")

    print("\nlargest source files:")
    print(f"  {'category':<20} {'file':<38} {'lines':>9}  {'cols':>4} {'num%':>5}  shape")
    for entry in files[:40]:
        shape = "table" if entry["tabular"] else "prose"
        print(f"  {entry['category'][:19]:<20} {entry['source_file'][:37]:<38} "
              f"{entry['lines']:>9,}  {entry['columns']:>4} "
              f"{entry['numeric_ratio']:>5.2f}  {shape}")

    print("\nheaders of the largest tabular files:")
    for entry in [f for f in files if f["tabular"]][:20]:
        print(f"  {entry['source_file']}: {entry['header'][:150]}")

    print("\nsample prose lines:")
    for entry in [f for f in files if not f["tabular"]][:8]:
        sample = next((s for s in entry["sample"] if len(s) > 40), "")
        print(f"  [{entry['category']}] {entry['source_file']}: {sample[:180]}")

    if args.out:
        args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
