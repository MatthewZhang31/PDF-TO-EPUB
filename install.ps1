<#
.SYNOPSIS
    Restore the pdf-to-epub workflow on a new machine.

.DESCRIPTION
    Run this after cloning the repository into the DSH user skill directory:

        git clone <repo-url> "$env:USERPROFILE\.dsh\skills\pdf-to-epub"
        powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\.dsh\skills\pdf-to-epub\install.ps1"

    It checks the Python runtime, installs the pinned dependencies (through a
    mirror, because the default index is unreachable on some networks), and runs
    the self-test so you know the toolkit works before converting a book.

    Pass -Mirror https://pypi.org/simple on a machine where the default index
    works. Pass -SkipInstall when the dependencies are already present.

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
# 'python' is not always on PATH on Windows even when Python is installed; the
# official installer registers the 'py' launcher instead.
$pythonCmd = $null
$pythonArgs = @()
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
    $pythonArgs = @("-3")
}
if (-not $pythonCmd) {
    Write-Error "Python was not found (neither 'python' nor 'py'). Install Python 3.10+ and re-run."
}
$version = (& $pythonCmd @pythonArgs -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
Write-Host "python: $pythonCmd $($pythonArgs -join ' ') (version $version)"
if ([version]$version -lt [version]"3.10") {
    Write-Error "Python 3.10 or newer is required; found $version."
}

# Some consoles are cp936 and crash on CJK output; keep the whole toolkit UTF-8.
$env:PYTHONIOENCODING = "utf-8"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------- skill path
# DSH scans $DSH_HOME/skills (default ~/.dsh/skills). If DSH_HOME points
# elsewhere, a clone into ~/.dsh/skills will never be discovered.
$expected = Join-Path $env:USERPROFILE ".dsh\skills\pdf-to-epub"
if ($env:DSH_HOME -and ($env:DSH_HOME.TrimEnd('\') -ne (Join-Path $env:USERPROFILE ".dsh"))) {
    $alt = Join-Path $env:DSH_HOME "skills\pdf-to-epub"
    Write-Warning "DSH_HOME is set to '$env:DSH_HOME'."
    Write-Warning "The skill is only discovered from `$DSH_HOME\skills, so this copy"
    Write-Warning "will be ignored. Move or clone it to: $alt"
} elseif ((Resolve-Path $here).Path.TrimEnd('\') -ne (Resolve-Path $expected -ErrorAction SilentlyContinue).Path.TrimEnd('\')) {
    Write-Warning "This repository is not at the path DSH scans."
    Write-Warning "  now:      $here"
    Write-Warning "  expected: $expected"
    Write-Warning "The command line will still work; the skill will only be picked up"
    Write-Warning "automatically from the expected path."
}

# ---------------------------------------------------------------- packages
if (-not $SkipInstall) {
    Write-Host "`ninstalling dependencies from $Mirror ..." -ForegroundColor Cyan
    & $pythonCmd @pythonArgs -m pip install --disable-pip-version-check -i $Mirror -r $req
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed. Check the mirror URL or your network."
    }
} else {
    Write-Host "`nskipping dependency install (-SkipInstall)"
}

# ---------------------------------------------------------------- verify
Write-Host "`nverifying imports ..." -ForegroundColor Cyan
& $pythonCmd @pythonArgs -c @"
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
    & $pythonCmd @pythonArgs (Join-Path $here "scripts\selftest.py")
    if ($LASTEXITCODE -ne 0) { Write-Error "self-test failed." }
}

# ---------------------------------------------------------------- report
Write-Host "`n== ready ==" -ForegroundColor Green
Write-Host @"
Convert a book with:

  `$env:PYTHONIOENCODING="utf-8"
  $pythonCmd $($pythonArgs -join ' ') "$here\scripts\pdf2epub.py" all --pdf "<book.pdf>" --out "<workdir>"

In any new DSH session you can also just ask: "convert this PDF to EPUB".
The `pdf-to-epub` skill is discovered from ~/.dsh/skills automatically.
"@
