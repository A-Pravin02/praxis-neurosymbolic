"""HYBRID. Administer a company's knowledge without touching application code.

An enterprise changes its policies, rules and records far more often than its software.
Everything the neuro-symbolic assistant knows lives in the knowledge pack, and this module
is the only way the product changes it:

* **Validate before writing.** A proposed change is applied to a copy of the whole pack,
  which is then loaded exactly as the assistant loads it: the rule grammar and
  stratification, every rule citing a clause that exists, every goal provable, the graph
  parsing, every attribute declared. A change that breaks any of it is rejected with the
  reason, and nothing is written.
* **Keep history.** Before a change is written, the files it replaces are copied to a
  timestamped snapshot, so any revision can be inspected and restored.
* **Take effect everywhere.** A revision marker changes with every write; the service
  reloads the pack when it sees a new revision.
* **Structured record edits.** Entities and relationships are added through an overlay file
  (`knowledge/zz_admin.yaml`), so the curated base files and their comments are never
  rewritten by a form.

It never executes anything it is given: rules are parsed by the rule grammar, policies
are text, records are YAML read with `safe_load`.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.core.errors import RuleError

EDITABLE = {"policies": ".md", "rules": ".rules", "knowledge": ".yaml"}
OVERLAY = "zz_admin.yaml"
HISTORY = ".history"
REVISION = "REVISION"
_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,80}\.(md|rules|yaml)$")
_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_LOCK = threading.Lock()


class KnowledgeChangeError(ValueError):
    """A proposed change that would break the knowledge pack."""


@dataclass
class Validation:
    ok: bool
    errors: list[str]
    summary: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": self.errors, "summary": self.summary}


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def revision(directory: Path) -> str:
    marker = directory / REVISION
    return marker.read_text(encoding="utf-8").strip() if marker.exists() else "initial"


def list_files(directory: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for folder, suffix in EDITABLE.items():
        out[folder] = [{"name": p.name, "bytes": p.stat().st_size}
                       for p in sorted((directory / folder).glob(f"*{suffix}"))]
    return out


def read_file(directory: Path, folder: str, name: str) -> str:
    return _path(directory, folder, name).read_text(encoding="utf-8")


def _path(directory: Path, folder: str, name: str) -> Path:
    if folder not in EDITABLE or not _NAME.match(name) or not name.endswith(EDITABLE[folder]):
        raise KnowledgeChangeError(f"{folder}/{name} is not an editable knowledge file")
    path = (directory / folder / name).resolve()
    if path.parent != (directory / folder).resolve():
        raise KnowledgeChangeError("invalid path")
    return path


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def validate(directory: Path, changes: dict[tuple[str, str], str | None]) -> Validation:
    """Load the pack with the changes applied, without touching the real pack."""
    with tempfile.TemporaryDirectory(prefix="praxis-pack-") as tmp:
        staged = Path(tmp) / directory.name
        shutil.copytree(directory, staged, ignore=shutil.ignore_patterns(HISTORY))
        for (folder, name), content in changes.items():
            target = _path(staged, folder, name)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                target.write_text(content, encoding="utf-8")
        return _check(staged)


def _check(directory: Path) -> Validation:
    from app.neural.retrieval import parse_policies
    from app.symbolic.dsl import load_program
    from app.symbolic.knowledge_graph import load_graph_dir

    errors: list[str] = []
    summary: dict[str, Any] = {}
    try:
        program = load_program(directory / "rules")
    except RuleError as exc:
        return Validation(False, [f"rules: {exc}"], {})
    try:
        graph = load_graph_dir(directory / "knowledge")
    except Exception as exc:  # noqa: BLE001 - any parse failure is a validation error
        return Validation(False, [f"knowledge graph: {exc}"], {})
    clauses = parse_policies(directory / "policies", as_of=graph.as_of)
    clause_ids = {c.id for c in clauses}
    for spec in program.rules:
        missing = [c for c in spec.clauses if c not in clause_ids]
        if missing:
            errors.append(f"rule {spec.code} cites clauses that no policy defines: "
                          f"{', '.join(missing)}")
    declared = set(program.attributes) | set(program.inputs)
    for entity in graph.entities.values():
        if entity.type not in program.entity_types:
            errors.append(f"entity {entity.id} has undeclared type {entity.type!r}")
        for attribute in entity.attributes:
            if attribute not in declared:
                errors.append(f"entity {entity.id} uses undeclared attribute {attribute!r}")
            elif attribute in program.attributes:
                value_type = program.attributes[attribute].value_type
                value = entity.attributes[attribute]
                if value_type in program.entity_types and str(value) not in graph.entities:
                    errors.append(f"entity {entity.id}: {attribute} points at {value!r}, "
                                  f"which is not a {value_type} in the graph")
    for binding in program.versions.values():
        if not graph.policy_versions(binding.policy):
            errors.append(f"version {binding.predicate} names policy {binding.policy!r}, which "
                          f"has no policy_version entities")
    summary = {"rules": len(program.rules), "goals": len(program.goals),
               "entities": len(graph.entities), "clauses": len(clauses),
               "policy_versions": sum(1 for _ in graph.entities_of_type("policy_version"))}
    return Validation(not errors, errors, summary)


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def apply(directory: Path, changes: dict[tuple[str, str], str | None], *,
          author: str, note: str = "") -> dict[str, Any]:
    with _LOCK:
        result = validate(directory, changes)
        if not result.ok:
            raise KnowledgeChangeError("; ".join(result.errors))
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        snapshot = directory / HISTORY / stamp
        for folder, name in changes:
            current = _path(directory, folder, name)
            if current.exists():
                (snapshot / folder).mkdir(parents=True, exist_ok=True)
                shutil.copy2(current, snapshot / folder / name)
        (snapshot).mkdir(parents=True, exist_ok=True)
        (snapshot / "CHANGE.yaml").write_text(yaml.safe_dump({
            "author": author, "note": note, "at": stamp,
            "files": [f"{f}/{n}" for f, n in changes],
            "replaced_revision": revision(directory)}, sort_keys=False), encoding="utf-8")
        for (folder, name), content in changes.items():
            target = _path(directory, folder, name)
            if content is None:
                target.unlink(missing_ok=True)
            else:
                tmp = target.with_suffix(target.suffix + ".tmp")
                tmp.write_text(content, encoding="utf-8")
                tmp.replace(target)
        (directory / REVISION).write_text(stamp, encoding="utf-8")
        return {"revision": stamp, "summary": result.summary}


def history(directory: Path) -> list[dict[str, Any]]:
    root = directory / HISTORY
    if not root.exists():
        return []
    entries = []
    for folder in sorted(root.iterdir(), reverse=True):
        meta = folder / "CHANGE.yaml"
        if meta.exists():
            entries.append({"id": folder.name, **(yaml.safe_load(meta.read_text("utf-8")) or {})})
    return entries


def restore(directory: Path, snapshot_id: str, *, author: str) -> dict[str, Any]:
    if not re.match(r"^\d{8}T\d{12}Z$", snapshot_id):
        raise KnowledgeChangeError("invalid snapshot id")
    snapshot = directory / HISTORY / snapshot_id
    if not snapshot.exists():
        raise KnowledgeChangeError("no such snapshot")
    changes: dict[tuple[str, str], str | None] = {}
    meta = yaml.safe_load((snapshot / "CHANGE.yaml").read_text("utf-8")) or {}
    for entry in meta.get("files", []):
        folder, name = entry.split("/", 1)
        saved = snapshot / folder / name
        # A file the change created did not exist before it: restoring removes it.
        changes[(folder, name)] = saved.read_text("utf-8") if saved.exists() else None
    return apply(directory, changes, author=author, note=f"restore {snapshot_id}")


# ---------------------------------------------------------------------------
# structured record edits
# ---------------------------------------------------------------------------

def upsert_entity(directory: Path, *, entity_id: str, entity_type: str, name: str | None,
                  attributes: dict[str, Any], author: str) -> dict[str, Any]:
    if not _ID.match(entity_id):
        raise KnowledgeChangeError("entity ids use letters, digits, '-' and '_' only")
    overlay_path = directory / "knowledge" / OVERLAY
    data = yaml.safe_load(overlay_path.read_text("utf-8")) if overlay_path.exists() else None
    data = data or {"entities": []}
    entities = data.setdefault("entities", [])
    existing = next((e for e in entities if str(e.get("id")) == entity_id), None)
    clean = {str(k): _plain(v) for k, v in attributes.items()}
    if existing is None:
        entities.append({"id": entity_id, "type": entity_type,
                         **({"name": name} if name else {}), "attributes": clean})
    else:
        if name:
            existing["name"] = name
        existing.setdefault("attributes", {}).update(clean)
    content = ("# Changes made through the knowledge administration console. Entries here\n"
               "# add entities or override attributes of entities in the base files.\n"
               + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return apply(directory, {("knowledge", OVERLAY): content}, author=author,
                 note=f"upsert {entity_type} {entity_id}")


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return dt.date.fromisoformat(value)
        return value
    raise KnowledgeChangeError("attribute values must be text, numbers, true/false or dates")
