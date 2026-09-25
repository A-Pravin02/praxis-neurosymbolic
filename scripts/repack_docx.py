"""Repack a .docx so Windows and Word recognise it as a document, not an archive.

OPC requires [Content_Types].xml to be the first part in the package. docx-js emitted
bare directory entries ("word/") ahead of it, which leaves Explorer treating the file as
a folder. This rewrites the zip with the content-types part first and drops the directory
entries, which a zip reader infers from the file paths anyway. Content is copied byte for
byte; nothing in the document changes.
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

CONTENT_TYPES = "[Content_Types].xml"


def repack(path: Path) -> None:
    with zipfile.ZipFile(path) as source:
        entries = [i for i in source.infolist() if not i.filename.endswith("/")]
        if CONTENT_TYPES not in {i.filename for i in entries}:
            raise SystemExit(f"{path.name}: no {CONTENT_TYPES}; not an OPC package")
        payload = {i.filename: source.read(i.filename) for i in entries}

    ordered = [CONTENT_TYPES] + [n for n in payload if n != CONTENT_TYPES]
    temporary = path.with_suffix(".repacked")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as target:
        for name in ordered:
            target.writestr(name, payload[name])
    shutil.move(str(temporary), str(path))

    with zipfile.ZipFile(path) as check:
        names = check.namelist()
        assert names[0] == CONTENT_TYPES, names[0]
        assert not any(n.endswith("/") for n in names)
        assert check.testzip() is None
    print(f"{path.name}: first entry is {names[0]}, {len(names)} parts, integrity OK")


if __name__ == "__main__":
    for argument in sys.argv[1:]:
        repack(Path(argument))
