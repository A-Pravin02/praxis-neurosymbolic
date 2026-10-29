# Export a .docx to PDF through Word, and report the page count.
#
#   powershell -ExecutionPolicy Bypass -File scripts/export_pdf.ps1 docs/paper/PRAXIS_IEEE.docx
#
# Word is used rather than a converter because the paper's layout depends on Word's own
# column breaking and table pagination: a PDF produced by anything else would not show
# the breaks a reviewer sees. The page count is printed because the paper has a six-page
# limit and an edit that overruns it must fail visibly rather than quietly.

param(
    [Parameter(Mandatory = $true)][string]$Docx,
    [string]$Pdf,
    # Fail if the document is longer than this. The IEEE paper has a six-page limit;
    # the project report has none, so the check is opt-in rather than assumed.
    [int]$MaxPages = 0
)

$ErrorActionPreference = "Stop"
$source = (Resolve-Path $Docx).Path
if (-not $Pdf) { $Pdf = [System.IO.Path]::ChangeExtension($source, ".pdf") }
else { $Pdf = [System.IO.Path]::GetFullPath($Pdf) }

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0
try {
    $doc = $word.Documents.Open($source, [ref]$false, [ref]$true)   # ConfirmConversions, ReadOnly
    try {
        # Repaginate before counting: the count is read from the laid-out document, not
        # from whatever was cached in the file.
        $doc.Repaginate()
        $pages = $doc.ComputeStatistics(2)        # wdStatisticPages
        $doc.SaveAs([ref]$Pdf, [ref]17)           # wdFormatPDF
        "exported : $Pdf"
        "pages    : $pages"
        if ($MaxPages -gt 0 -and $pages -gt $MaxPages) {
            Write-Warning "$pages pages - the limit for this document is $MaxPages."
            exit 2
        }
    }
    finally { $doc.Close([ref]$false) }
}
finally {
    $word.Quit()
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}
