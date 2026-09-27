"""Structural checks on the paper, since no TeX installation is available here.

This cannot replace compiling. It catches the mistakes that are cheap to make while
editing -- unbalanced environments, references to labels that do not exist, citations
with no bibliography entry -- and says so rather than implying the document builds.
"""
import re
import sys
from pathlib import Path

PAPER = Path("C:/Users/aprav/OneDrive/Desktop/praxisvit/docs/paper")
tex = (PAPER / "main.tex").read_text(encoding="utf-8")
bib = (PAPER / "references.bib").read_text(encoding="utf-8")

# Strip comments so a commented-out \begin does not count.
body = "\n".join(re.sub(r"(?<!\\)%.*$", "", line) for line in tex.splitlines())

print(f"lines: {len(tex.splitlines())}")
n_sec = len(re.findall(r"\\section\{", body))
n_sub = len(re.findall(r"\\subsection\{", body))
print("sections:", n_sec, " subsections:", n_sub)

bad = 0
for env in ("document", "abstract", "table", "itemize", "enumerate", "figure",
            "equation", "align", "tabular"):
    opened = len(re.findall(r"\\begin\{" + env + r"\}", body))
    closed = len(re.findall(r"\\end\{" + env + r"\}", body))
    if opened != closed:
        print(f"  UNBALANCED {env}: {opened} begin, {closed} end")
        bad += 1

labels = set(re.findall(r"\\label\{([^}]+)\}", body))
refs = set(re.findall(r"\\ref\{([^}]+)\}", body))
missing = sorted(refs - labels)
if missing:
    print(f"  REF WITH NO LABEL: {missing}")
    bad += len(missing)

keys = set(re.findall(r"@\w+\{([^,]+),", bib))
cited = set()
for group in re.findall(r"\\cite[tp]?\{([^}]+)\}", body):
    cited.update(k.strip() for k in group.split(","))
uncited_missing = sorted(cited - keys)
if uncited_missing:
    print(f"  CITED BUT NOT IN BIB: {uncited_missing}")
    bad += len(uncited_missing)

unused = sorted(keys - cited)
print(f"labels: {len(labels)}  refs: {len(refs)}  bib entries: {len(keys)}  "
      f"cited: {len(cited)}")
if unused:
    print(f"  (in bib, never cited: {unused})")

print("\nstructural checks passed" if not bad else f"\n{bad} problem(s) above")
sys.exit(1 if bad else 0)
