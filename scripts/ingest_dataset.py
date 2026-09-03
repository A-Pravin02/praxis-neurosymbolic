#!/usr/bin/env python
"""Ingest a dataset into a workspace.

    python scripts/ingest_dataset.py <path> --tenant acme [options]

`path` may be a single file or a directory, which is walked recursively. Every
supported file type is parsed, normalised, chunked, screened for prompt
injection, embedded, indexed, and mined for facts and candidate rules. Nothing
about the dataset's schema is assumed: structured records are mapped through a
`RecordMapping`, supplied inline or in a JSON mapping file.

    --mapping mapping.json      declarative column -> predicate mapping
    --profile                   describe the dataset and exit, ingesting nothing
    --dry-run                   parse and report, without writing

A mapping file looks like:

    {
      "subject_field": "employee_id",
      "subject_prefix": "employee:",
      "exclude_fields": ["notes"],
      "rename": {"tenure_years": "years_of_service"},
      "units": {"tenure_years": "years"},
      "sensitivity": "internal",
      "required_permission": null
    }
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.auth.service import resolve_principal  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.models.tenancy import TenantLookup, User  # noqa: E402
from app.db.session import SessionLocal, bind_tenant  # noqa: E402
from app.knowledge.extraction import RecordMapping  # noqa: E402
from app.rag.ingest import IngestOptions, ingest_bytes  # noqa: E402
from app.rag.parsers import SUPPORTED_EXTENSIONS, parse_bytes  # noqa: E402


def resolve_principal_for_tenant(tenant_slug: str, email: str | None):
    session = SessionLocal()
    try:
        bind_tenant(session, None)
        routing = session.get(TenantLookup, tenant_slug)
        if routing is None:
            raise SystemExit(f"No workspace with slug {tenant_slug!r}.")
        bind_tenant(session, routing.tenant_id)
        stmt = select(User).where(User.tenant_id == routing.tenant_id)
        if email:
            stmt = stmt.where(User.email == email.lower())
        user = session.scalars(stmt.order_by(User.created_at)).first()
        if user is None:
            raise SystemExit("No user found to ingest as. Create one first.")
        principal = resolve_principal(session, user)
        if "document:write" not in principal.permissions:
            raise SystemExit(
                f"{user.email} lacks document:write. Pass --as with an admin account.")
        return principal
    finally:
        session.close()


def discover(path: pathlib.Path) -> list[pathlib.Path]:
    if path.is_file():
        return [path]
    files = [
        candidate for candidate in sorted(path.rglob("*"))
        if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return files


def profile(files: list[pathlib.Path]) -> dict:
    """Describe a dataset before deciding how to treat it."""
    report: dict = {
        "files": len(files),
        "by_extension": dict(Counter(f.suffix.lower() for f in files)),
        "total_bytes": sum(f.stat().st_size for f in files),
        "structured_files": [],
        "text_files": [],
        "columns": {},
    }
    for file in files[:200]:
        try:
            parsed = parse_bytes(file.read_bytes(), filename=file.name)
        except Exception as exc:  # noqa: BLE001
            report.setdefault("unreadable", []).append(f"{file.name}: {type(exc).__name__}")
            continue
        if parsed.records:
            report["structured_files"].append(file.name)
            columns = list(parsed.records[0].keys())
            report["columns"][file.name] = columns
        else:
            report["text_files"].append(file.name)
    report["suggestion"] = (
        "Structured files should be mapped to facts with --mapping; text files go "
        "to retrieval and pattern extraction. Review the candidate rules the "
        "ingester reports before activating any of them."
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", type=pathlib.Path)
    parser.add_argument("--tenant", default="acme", help="workspace slug")
    parser.add_argument("--as", dest="email", default=None,
                        help="email of the user to ingest as (needs document:write)")
    parser.add_argument("--collection", default="default")
    parser.add_argument("--sensitivity", default="internal")
    parser.add_argument("--required-permission", default=None)
    parser.add_argument("--effective-date", default=None)
    parser.add_argument("--version-label", default="")
    parser.add_argument("--mapping", type=pathlib.Path, default=None)
    parser.add_argument("--fact-subject", default="policy",
                        help="subject for facts extracted from prose")
    parser.add_argument("--no-facts", action="store_true",
                        help="index for retrieval only, extract nothing")
    parser.add_argument("--profile", action="store_true",
                        help="describe the dataset and exit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    configure_logging()

    if not args.path.exists():
        raise SystemExit(f"{args.path} does not exist.")

    files = discover(args.path)
    if not files:
        raise SystemExit(
            f"No supported files found. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")

    if args.profile:
        print(json.dumps(profile(files), indent=2))
        return

    mapping = None
    if args.mapping:
        mapping = RecordMapping(**json.loads(args.mapping.read_text(encoding="utf-8")))

    if args.dry_run:
        print(json.dumps({"would_ingest": [f.name for f in files],
                          "count": len(files)}, indent=2))
        return

    principal = resolve_principal_for_tenant(args.tenant, args.email)
    options = IngestOptions(
        collection=args.collection, sensitivity=args.sensitivity,
        required_permission=args.required_permission,
        effective_date=args.effective_date, version_label=args.version_label,
        extract_facts=not args.no_facts, fact_subject=args.fact_subject,
        record_mapping=mapping,
    )

    totals = Counter()
    reports = []
    for file in files:
        session = SessionLocal()
        try:
            bind_tenant(session, principal.tenant_id, principal.user_id)
            report = ingest_bytes(session, principal, file.read_bytes(),
                                  filename=file.name, options=options)
            session.commit()
            reports.append(report.as_dict())
            totals["documents"] += 1
            totals["chunks"] += report.chunks
            totals["facts"] += report.facts
            totals["rule_candidates"] += report.rule_candidates
            totals["quarantined_chunks"] += report.quarantined_chunks
            print(f"  {file.name}: {report.chunks} chunks, {report.facts} facts, "
                  f"{report.rule_candidates} rule candidates"
                  + (f", {report.quarantined_chunks} quarantined"
                     if report.quarantined_chunks else ""))
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            totals["failed"] += 1
            print(f"  {file.name}: FAILED ({type(exc).__name__}: {exc})", file=sys.stderr)
        finally:
            session.close()

    print("\n" + json.dumps(dict(totals), indent=2))
    if totals["rule_candidates"]:
        print(
            "\nCandidate rules were recorded on the documents' metadata. They are NOT "
            "active: review them and create rules explicitly via POST /api/rules."
        )


if __name__ == "__main__":
    main()
