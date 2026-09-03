"""Sentence-aware chunking with section headings preserved.

Chunks carry their section heading and page so citations can point at something a
human can find, and consecutive chunks overlap so a fact split across a boundary
is still retrievable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.neuro.heuristics import sentences

_LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)


@dataclass
class Chunk:
    ordinal: int
    text: str
    section: str = ""
    page: int | None = None
    token_estimate: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


def estimate_tokens(text: str) -> int:
    return max(1, len(text.split()) * 4 // 3)


def _split_units(text: str) -> list[str]:
    """Sentences, but list items stay whole (they carry policy values)."""
    units: list[str] = []
    for block in re.split(r"\n{2,}", text):
        block = block.strip()
        if not block:
            continue
        if _LIST_ITEM.search(block):
            for line in block.splitlines():
                line = line.strip()
                if line:
                    units.append(line)
        else:
            units.extend(sentences(block) or [block])
    return units


def chunk_text(
    text: str, *, section: str = "", page: int | None = None,
    target_tokens: int | None = None, overlap_tokens: int | None = None,
    start_ordinal: int = 0,
) -> list[Chunk]:
    target = target_tokens or settings.chunk_target_tokens
    overlap = overlap_tokens or settings.chunk_overlap_tokens
    units = _split_units(text)
    if not units:
        return []

    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_tokens = 0
    ordinal = start_ordinal

    def flush() -> None:
        nonlocal buffer, buffer_tokens, ordinal
        if not buffer:
            return
        body = " ".join(buffer).strip()
        if body:
            chunks.append(Chunk(ordinal=ordinal, text=body, section=section, page=page,
                                token_estimate=estimate_tokens(body)))
            ordinal += 1
        # Carry the tail forward so a boundary does not cut a fact in half.
        carried: list[str] = []
        carried_tokens = 0
        for unit in reversed(buffer):
            unit_tokens = estimate_tokens(unit)
            if carried_tokens + unit_tokens > overlap:
                break
            carried.insert(0, unit)
            carried_tokens += unit_tokens
        buffer = carried
        buffer_tokens = carried_tokens

    for unit in units:
        unit_tokens = estimate_tokens(unit)
        if unit_tokens > target * 2:
            # A single very long unit: hard-split on words.
            flush()
            words = unit.split()
            step = max(50, target * 3 // 4)
            for i in range(0, len(words), step):
                piece = " ".join(words[i:i + step])
                chunks.append(Chunk(ordinal=ordinal, text=piece, section=section, page=page,
                                    token_estimate=estimate_tokens(piece)))
                ordinal += 1
            buffer, buffer_tokens = [], 0
            continue
        if buffer_tokens + unit_tokens > target and buffer:
            flush()
        buffer.append(unit)
        buffer_tokens += unit_tokens

    if buffer:
        body = " ".join(buffer).strip()
        if body and (not chunks or chunks[-1].text != body):
            chunks.append(Chunk(ordinal=ordinal, text=body, section=section, page=page,
                                token_estimate=estimate_tokens(body)))
    return chunks


def chunk_document(sections: list, *, target_tokens: int | None = None,
                   overlap_tokens: int | None = None) -> list[Chunk]:
    """Chunk a ParsedDocument's sections, keeping ordinals globally sequential."""
    out: list[Chunk] = []
    ordinal = 0
    for section in sections:
        heading = getattr(section, "heading", "") or ""
        body = getattr(section, "text", "") or ""
        page = getattr(section, "page", None)
        # Prefix short sections with their heading so the chunk is self-describing.
        content = f"{heading}\n{body}".strip() if heading else body.strip()
        if not content:
            continue
        produced = chunk_text(content, section=heading, page=page,
                              target_tokens=target_tokens, overlap_tokens=overlap_tokens,
                              start_ordinal=ordinal)
        out.extend(produced)
        ordinal += len(produced)
    return out
