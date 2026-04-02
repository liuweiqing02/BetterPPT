param(
  [string]$ApiBase = "http://127.0.0.1:8000/api/v1",
  [string]$FrontBase = "http://127.0.0.1:5173",
  [string]$SourcePdf = "",
  [string]$ReferencePptx = "",
  [int]$PollTimeoutSeconds = 360,
  [switch]$KeepServices,
  [switch]$KeepApi
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$refDir = Join-Path $root "ref"
if ([string]::IsNullOrWhiteSpace($SourcePdf)) {
  $defaultSourcePdf = Join-Path $refDir "DynaCollab.pdf"
  if (Test-Path $defaultSourcePdf) {
    $SourcePdf = $defaultSourcePdf
  } else {
    $pdfCandidates = Get-ChildItem -Path $refDir -Filter *.pdf -File -ErrorAction SilentlyContinue | Sort-Object Name
    if ($pdfCandidates.Count -gt 0) {
      $SourcePdf = $pdfCandidates[0].FullName
    } else {
      throw "No source pdf found under $refDir. Please pass -SourcePdf explicitly."
    }
  }
}
$backendPython = Join-Path $root "source\backend\.venv\Scripts\python.exe"
$runner = Join-Path $root "bin\full_regression_round.py"

if (-not (Test-Path $backendPython)) {
  throw "backend python not found: $backendPython"
}
if (-not (Test-Path $runner)) {
  throw "runner script not found: $runner"
}

$argsList = @(
  $runner,
  "--api-base", $ApiBase,
  "--front-base", $FrontBase,
  "--source-pdf", $SourcePdf,
  "--poll-timeout-seconds", "$PollTimeoutSeconds"
)

if (-not [string]::IsNullOrWhiteSpace($ReferencePptx)) {
  $argsList += @("--reference-pptx", $ReferencePptx)
}
if ($KeepServices) {
  $argsList += "--keep-services"
}
if ($KeepApi) {
  $argsList += "--keep-api"
}

& $backendPython @argsList
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
  exit $exitCode
}
