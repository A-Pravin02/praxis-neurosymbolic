# Rebuilding the two Word reports

The `.docx` reports in `docs/` are generated, not hand-edited. Editing the Word file
directly means the next build silently discards the change, so edit `build_reports.js`
instead.

```bash
cd scripts
cp reports-package.json package.json && npm install       # first time only
python make_report_figures.py figs                        # regenerate the figures
FIGDIR=figs node build_reports.js \
    ../docs/Project-1-Praxis-RAG-NeuroSymbolic.docx \
    ../../neurosymbolic-kbqa/docs/Project-2-NeuroSymbolic-KBQA.docx
python repack_docx.py ../docs/*.docx
```

`repack_docx.py` is not optional. `docx-js` writes bare directory entries ahead of
`[Content_Types].xml`, and OPC requires that part to come first — without the repack
Windows shows the file as a folder rather than opening it in Word. The script rewrites
the archive with the content-types part first and copies every part byte for byte, so
nothing in the document changes.

`make_report_figures.py` reads measurements from files on disk. A panel with no
measurement behind it says so on its face rather than showing a plausible-looking bar.

Every number in the reports comes from `docs/benchmark/`. When a run is repeated, update
the run files first and the report second, so the two never disagree.
