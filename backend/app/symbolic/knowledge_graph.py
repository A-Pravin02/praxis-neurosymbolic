"""The company knowledge graph: typed entities, facts, and traversable relations.

Every entity is a node. Every attribute is a fact `predicate(entity) = value`.
An attribute whose value is another entity is also an edge, which is what makes
multi-hop reasoning possible: customer -> order -> product -> category -> policy.

The graph is the *closed world* the symbolic reasoner trusts. If an order's
record says it shipped, it shipped - a user saying otherwise is a claim to be
checked against it, not a fact that overrides it.
"""
from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

COMPANY = "meridian"


@dataclass
class Entity:
    id: str
    type: str
    name: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactRef:
    """One fact the reasoning actually touched, with where it came from."""

    subject: str
    predicate: str
    value: Any
    source: str          # "record" | "stated" | "derived"
    rule: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"subject": self.subject, "predicate": self.predicate,
                "value": self.value, "source": self.source, "rule": self.rule}


def _to_ordinal(value: Any) -> Any:
    """Dates become day numbers so rules can subtract them."""
    if isinstance(value, dt.datetime):
        return value.date().toordinal()
    if isinstance(value, dt.date):
        return value.toordinal()
    return value


class KnowledgeGraph:
    def __init__(self, entities: Iterable[Entity], as_of: dt.date) -> None:
        self.as_of = as_of
        self.entities: dict[str, Entity] = {}
        self._facts: dict[tuple[str, str], list[Any]] = {}
        self._by_type: dict[str, list[str]] = {}
        self._edges: dict[str, list[tuple[str, str]]] = {}
        self._reverse: dict[str, list[tuple[str, str]]] = {}
        for entity in entities:
            self.add(entity)
        company = self.entities.get(COMPANY)
        if company is not None:
            self._set(COMPANY, "today", as_of.toordinal())

    # -- construction -------------------------------------------------------
    def add(self, entity: Entity) -> None:
        if entity.id in self.entities:
            raise ValueError(f"Duplicate entity id {entity.id!r}")
        entity.attributes = {k: _to_ordinal(v) for k, v in entity.attributes.items()}
        self.entities[entity.id] = entity
        self._by_type.setdefault(entity.type, []).append(entity.id)
        for predicate, value in entity.attributes.items():
            self._set(entity.id, predicate, value)

    def _set(self, subject: str, predicate: str, value: Any) -> None:
        self._facts.setdefault((subject, predicate), []).append(value)

    def link_edges(self) -> None:
        """Entity-valued attributes become edges once every node is known."""
        self._edges.clear()
        self._reverse.clear()
        for (subject, predicate), values in self._facts.items():
            for value in values:
                if isinstance(value, str) and value in self.entities and subject in self.entities:
                    self._edges.setdefault(subject, []).append((predicate, value))
                    self._reverse.setdefault(value, []).append((predicate, subject))

    # -- facts ----------------------------------------------------------------
    def values(self, subject: str, predicate: str) -> list[Any]:
        return list(self._facts.get((subject, predicate), []))

    def has_entity(self, entity_id: str) -> bool:
        return entity_id in self.entities

    def entities_of_type(self, entity_type: str) -> list[Entity]:
        return [self.entities[i] for i in self._by_type.get(entity_type, [])]

    def find(self, predicate: str, value: Any) -> list[Entity]:
        target = str(value).lower()
        return [self.entities[s] for (s, p), vals in self._facts.items()
                if p == predicate and s in self.entities
                and any(str(v).lower() == target for v in vals)]

    def facts_about(self, entity_id: str) -> dict[str, Any]:
        return {p: vals[0] for (s, p), vals in self._facts.items() if s == entity_id and vals}

    # -- graph traversal --------------------------------------------------------
    def neighbours(self, entity_id: str) -> list[tuple[str, str, str]]:
        """(direction, predicate, other) for every edge touching an entity."""
        out = [("out", p, o) for p, o in self._edges.get(entity_id, [])]
        out += [("in", p, s) for p, s in self._reverse.get(entity_id, [])]
        return out

    def related(self, entity_id: str, entity_type: str, *, max_hops: int = 3) -> list[str]:
        """Entities of a type reachable within a number of hops, nearest first."""
        seen = {entity_id}
        frontier = deque([(entity_id, 0)])
        found: list[str] = []
        while frontier:
            current, depth = frontier.popleft()
            if depth >= max_hops:
                continue
            for _, _, other in self.neighbours(current):
                if other in seen:
                    continue
                seen.add(other)
                if self.entities[other].type == entity_type:
                    found.append(other)
                frontier.append((other, depth + 1))
        return found

    def path(self, source: str, target: str, *, max_hops: int = 5) -> list[tuple[str, str, str]]:
        """Shortest relation path, as (from, predicate, to) hops."""
        if source == target:
            return []
        parents: dict[str, tuple[str, str, str]] = {}
        frontier = deque([source])
        seen = {source}
        while frontier:
            current = frontier.popleft()
            for direction, predicate, other in self.neighbours(current):
                if other in seen:
                    continue
                seen.add(other)
                hop = (current, predicate, other) if direction == "out" else \
                    (current, f"~{predicate}", other)
                parents[other] = hop
                if other == target:
                    route = []
                    node = target
                    while node != source:
                        route.append(parents[node])
                        node = parents[node][0]
                    route.reverse()
                    return route if len(route) <= max_hops else []
                frontier.append(other)
        return []

    def owner_of(self, entity_id: str) -> str | None:
        """The customer or employee a record belongs to, found by traversal."""
        entity = self.entities.get(entity_id)
        if entity is None:
            return None
        if entity.type in ("customer", "employee"):
            return entity_id
        for predicate in ("placed_by", "claimant"):
            values = self.values(entity_id, predicate)
            if values:
                return values[0]
        for predicate in ("return_for", "refund_for_return"):
            values = self.values(entity_id, predicate)
            if values:
                return self.owner_of(values[0])
        # A payment carries no owner of its own; it belongs to whoever placed the order
        # that was paid with it.
        for predicate, other in self._reverse.get(entity_id, []):
            if predicate == "paid_with":
                return self.owner_of(other)
        return None

    def holders_of(self, predicate: str, value: str) -> list[str]:
        """Subjects whose `predicate` points at `value`."""
        return [s for (s, p), values in self._facts.items()
                if p == predicate and value in map(str, values)]

    def policy_versions(self, policy: str) -> list[Entity]:
        """Every version of a policy, oldest first by effective date."""
        versions = [e for e in self.entities_of_type("policy_version")
                    if [str(v) for v in self.values(e.id, "policy")] == [policy]]
        return sorted(versions, key=lambda e: (self.values(e.id, "effective_from") or [0])[0])

    def view_for(self, kind: str, person_id: str | None, program) -> "AuthorizedGraph":
        return AuthorizedGraph(self, kind, person_id, program)

    def person_by_email(self, email: str) -> Entity | None:
        matches = self.find("email", email)
        return matches[0] if matches else None

    def records_of(self, person_id: str, entity_type: str) -> list[Entity]:
        return [e for e in self.entities_of_type(entity_type) if self.owner_of(e.id) == person_id]

    def display_name(self, entity_id: Any) -> str:
        entity = self.entities.get(str(entity_id)) if entity_id is not None else None
        return entity.name if entity else str(entity_id)

    def date_of(self, ordinal: Any) -> dt.date | None:
        try:
            return dt.date.fromordinal(int(ordinal))
        except (TypeError, ValueError, OverflowError):
            return None


class AuthorizedGraph:
    """The part of the knowledge graph one person is allowed to reason over.

    Authorization happens *before* reasoning: the reasoner, the grounding and the
    verifier receive this view, not the graph, so a fact the person may not see cannot
    enter a proof, a why-not explanation or a number in the answer. Visibility is declared
    in the schema (`access customer own: order ...`), not written in code:

    * `public` types (products, categories, policy versions) are visible to everyone;
    * `own` types are visible when the record belongs to the person asking;
    * `all` types are visible whoever they belong to (a support agent and orders).
    """

    def __init__(self, graph: KnowledgeGraph, kind: str, person_id: str | None, program) -> None:
        self.base = graph
        self.kind = kind
        self.person_id = person_id
        self.as_of = graph.as_of
        public = program.visible_types("public", "all")
        self.all_types = public | program.visible_types(kind, "all")
        self.own_types = program.visible_types(kind, "own")
        self.unrestricted = "*" in self.all_types
        self._visible: dict[str, bool] = {}
        self.entities = {i: e for i, e in graph.entities.items() if self.visible(i)}

    # -- visibility -----------------------------------------------------------
    def visible(self, entity_id: str) -> bool:
        if entity_id in self._visible:
            return self._visible[entity_id]
        entity = self.base.entities.get(entity_id)
        if entity is None:
            allowed = False
        elif self.unrestricted or entity.type in self.all_types:
            allowed = True
        elif entity.type in self.own_types and self.person_id is not None:
            allowed = self.base.owner_of(entity_id) == self.person_id
        else:
            allowed = False
        self._visible[entity_id] = allowed
        return allowed

    def hidden_count(self) -> int:
        return len(self.base.entities) - len(self.entities)

    # -- the KnowledgeGraph interface, filtered -------------------------------------
    def values(self, subject: str, predicate: str) -> list[Any]:
        if subject in self.base.entities and not self.visible(subject):
            return []
        return [v for v in self.base.values(subject, predicate)
                if not (isinstance(v, str) and v in self.base.entities and not self.visible(v))]

    def has_entity(self, entity_id: str) -> bool:
        return entity_id in self.entities

    def entities_of_type(self, entity_type: str) -> list[Entity]:
        return [e for e in self.base.entities_of_type(entity_type) if self.visible(e.id)]

    def find(self, predicate: str, value: Any) -> list[Entity]:
        return [e for e in self.base.find(predicate, value) if self.visible(e.id)]

    def facts_about(self, entity_id: str) -> dict[str, Any]:
        return self.base.facts_about(entity_id) if self.visible(entity_id) else {}

    def neighbours(self, entity_id: str) -> list[tuple[str, str, str]]:
        if not self.visible(entity_id):
            return []
        return [n for n in self.base.neighbours(entity_id) if self.visible(n[2])]

    def related(self, entity_id: str, entity_type: str, *, max_hops: int = 3) -> list[str]:
        if not self.visible(entity_id):
            return []
        return [r for r in self.base.related(entity_id, entity_type, max_hops=max_hops)
                if self.visible(r)]

    def path(self, source: str, target: str, *, max_hops: int = 5):
        route = self.base.path(source, target, max_hops=max_hops)
        return route if all(self.visible(a) and self.visible(b) for a, _, b in route) else []

    def owner_of(self, entity_id: str) -> str | None:
        return self.base.owner_of(entity_id) if self.visible(entity_id) else None

    def holders_of(self, predicate: str, value: str) -> list[str]:
        return [s for s in self.base.holders_of(predicate, value) if self.visible(s)]

    def policy_versions(self, policy: str) -> list[Entity]:
        return [e for e in self.base.policy_versions(policy) if self.visible(e.id)]

    def person_by_email(self, email: str) -> Entity | None:
        person = self.base.person_by_email(email)
        return person if person is not None and self.visible(person.id) else None

    def records_of(self, person_id: str, entity_type: str) -> list[Entity]:
        return [e for e in self.base.records_of(person_id, entity_type) if self.visible(e.id)]

    def display_name(self, entity_id: Any) -> str:
        if entity_id is not None and str(entity_id) in self.base.entities and \
                not self.visible(str(entity_id)):
            return "a restricted record"
        return self.base.display_name(entity_id)

    def date_of(self, ordinal: Any) -> dt.date | None:
        return self.base.date_of(ordinal)

    def view_for(self, kind: str, person_id: str | None, program) -> "AuthorizedGraph":
        return AuthorizedGraph(self.base, kind, person_id, program)


def load_graph(path: Path) -> KnowledgeGraph:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    as_of = data.get("as_of")
    if isinstance(as_of, str):
        as_of = dt.date.fromisoformat(as_of)
    entities = [Entity(id=str(raw["id"]), type=raw["type"], name=raw.get("name", raw["id"]),
                       attributes=dict(raw.get("attributes") or {}))
                for raw in data.get("entities", [])]
    graph = KnowledgeGraph(entities, as_of=as_of or dt.date.today())
    graph.link_edges()
    return graph


@lru_cache(maxsize=4)
def load_graph_cached(path: str) -> KnowledgeGraph:
    return load_graph(Path(path))
