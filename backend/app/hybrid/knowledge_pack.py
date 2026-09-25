"""A company's knowledge, loaded once: rules, graph, policy text, and understanding.

Everything company-specific lives in a pack directory:

    policies/*.md      policy text, clause-numbered          -> neural retrieval
    rules/*.rules      schema, goals, inputs and rules        -> symbolic reasoning
    knowledge/*.yaml   entities and relations                 -> knowledge graph
    benchmark/*.yaml   evaluation questions

Swapping the directory swaps the company. No module under app/ names a Meridian
concept.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.neural.encoder import Encoder, get_encoder
from app.neural.query_understanding import QueryUnderstanding
from app.neural.retrieval import PolicyIndex, parse_policies
from app.symbolic.dsl import KnowledgeProgram, load_program
from app.symbolic.knowledge_graph import KnowledgeGraph, load_graph_dir

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class KnowledgePack:
    name: str
    directory: Path
    program: KnowledgeProgram
    graph: KnowledgeGraph
    policies: PolicyIndex
    understanding: QueryUnderstanding
    encoder: Encoder


def default_pack_dir() -> Path:
    if settings.knowledge_pack_dir:
        return Path(settings.knowledge_pack_dir)
    return BACKEND_ROOT / "data" / "meridian"


@lru_cache(maxsize=4)
def load_pack(directory: str | None = None, encoder_kind: str | None = None) -> KnowledgePack:
    root = Path(directory) if directory else default_pack_dir()
    program = load_program(root / "rules")
    graph = load_graph_dir(root / "knowledge")
    # Ownership is pack data: an order belongs to the customer who placed it, a claim to
    # the member who made it. The graph ships the retail defaults so the original pack
    # needs no change, and a pack that declares its own replaces them.
    if program.principal_types:
        graph.principal_types = program.principal_types
    if program.owner_predicates:
        graph.owner_predicates = program.owner_predicates
    encoder = get_encoder(encoder_kind)
    return KnowledgePack(
        name=root.name,
        directory=root,
        program=program,
        graph=graph,
        policies=PolicyIndex(parse_policies(root / "policies", as_of=graph.as_of), encoder),
        understanding=QueryUnderstanding(program, graph, encoder),
        encoder=encoder,
    )
