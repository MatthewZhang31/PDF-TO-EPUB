<#
.SYNOPSIS
    Restore the pdf-to-epub workflow on a new machine.

.DESCRIPTION
    Run this after cloning the repository into the DSH user skill directory:

        git clone <repo-url> "$env:USERPROFILE\.dsh\skills\pdf-to-epub"
        powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.dsh\skills\pdf-to-epub\install.ps1"

    It checks the Python runtime, installs the pinned dependencies (through a
    mirror, because the default index is unreachable on this network), and runs
    the self-test so you know the toolkit works before converting a book.

    The skill is discovered from the fixed path ~/.dsh/skills/<name>/SKILL.md,
    so no registration step is needed: a new DSH session picks it up
    automatically.
#>
[CmdletBinding()]
param(
    [string]$Mirror = "https://pypi.tuna.tsinghua.edu.cn/simple",
    [switch]$SkipInstall,
    [switch]$SkipTest
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$req = Join-Path $here "scripts\requirements.txt"

Write-Host "== pdf-to-epub installer ==" -ForegroundColor Cyan
Write-Host "repo: $here"

# ---------------------------------------------------------------- python
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Error "Python was not found on PATH. Install Python 3.10+ and re-run."
}
$version = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
Write-Host "python: $python (version $version)"
if ([version]$version -lt [version]"3.10") {
    Write-Error "Python 3.10 or newer is required; found $version."
}

# Some consoles are cp936 and crash on CJK output; keep the whole toolkit UTF-8.
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---------------------------------------------------------------- packages
if (-not $SkipInstall) {
    Write-Host "`ninstalling dependencies from $Mirror ..." -ForegroundColor Cyan
    & python -m pip install --disable-pip-version-check -i $Mirror -r $req
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed. Check the mirror URL or your network."
    }
} else {
    Write-Host "`nskipping dependency install (-SkipInstall)"
}

# ---------------------------------------------------------------- verify
Write-Host "`nverifying imports ..." -ForegroundColor Cyan
& python -c @"
import importlib.util, sys
need = ['pymupdf', 'PIL', 'numpy']
missing = [m for m in need if importlib.util.find_spec(m) is None]
if missing:
    print('MISSING:', ', '.join(missing)); sys.exit(1)
print('core ok: pymupdf, pillow, numpy')
try:
    import rapidocr_onnxruntime  # noqa: F401
    print('ocr  ok: rapidocr-onnxruntime')
except Exception:
    print('ocr  MISSING: rapidocr-onnxruntime (only needed for PDFs with no text layer)')
"@
if ($LASTEXITCODE -ne 0) {
    Write-Error "Required packages are missing. Re-run without -SkipInstall."
}

if (-not $SkipTest) {
    Write-Host "`nrunning self-test ..." -ForegroundColor Cyan
    & python (Join-Path $here "scripts\selftest.py")
    if ($LASTEXITCODE -ne 0) { Write-Error "self-test failed." }
}

# ---------------------------------------------------------------- report
Write-Host "`n== ready ==" -ForegroundColor Green
Write-Host @"
Convert a book with:

  `$env:PYTHONIOENCODING="utf-8"
  python "$here\scripts\pdf2epub.py" all --pdf "<book.pdf>" --out "<workdir>"

In any new DSH session you can also just ask: "convert this PDF to EPUB".
The `pdf-to-epub` skill is discovered from ~/.dsh/skills automatically.
"@
