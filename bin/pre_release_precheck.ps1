param(
  [switch]$SkipE2E
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backendDir = Join-Path $repoRoot "source\backend"
$pythonExe = Join-Path $backendDir ".venv\Scripts\python.exe"
$ok = $true

function Write-Step([string]$name) {
  Write-Host ""
  Write-Host "==> $name"
}

function Fail-Step([string]$message) {
  $script:ok = $false
  Write-Host "FAIL: $message" -ForegroundColor Red
}

function Run-Command([string]$name, [scriptblock]$cmd) {
  Write-Step $name
  try {
    & $cmd
    Write-Host "PASS: $name" -ForegroundColor Green
  } catch {
    Fail-Step "$name error: $($_.Exception.Message)"
  }
}

Write-Host "Pre-release precheck starting..."
Write-Host "Repo root: $repoRoot"
Write-Host "Backend dir: $backendDir"

if (!(Test-Path $pythonExe)) {
  throw "Python venv not found: $pythonExe"
}

Run-Command "Check required migration files" {
  $required = @(
    "001_init.sql",
    "002_v12_create_template_slot_definitions.sql",
    "003_v12_create_task_mapping_and_filling_tables.sql",
    "004_v12_create_task_quality_reports.sql",
    "005_v12_alter_tasks_and_files.sql",
    "006_v12_task_steps_attempt_expand.sql",
    "007_v12_task_steps_drop_legacy_unique_contract.sql"
  )
  foreach ($f in $required) {
    $p = Join-Path $backendDir "migrations\$f"
    if (!(Test-Path $p)) {
      throw "Missing migration: $p"
    }
  }
}

Run-Command "Static compile (backend + regression scripts)" {
  & $pythonExe -m py_compile `
    "$backendDir\app\core\constants.py" `
    "$backendDir\app\workers\runner.py" `
    "$backendDir\app\services\task_service.py" `
    "$backendDir\app\api\routes\tasks.py" `
    "$backendDir\app\schemas\task.py" `
    "$repoRoot\bin\page_acceptance_round.py" `
    "$repoRoot\bin\api_contract_and_actions_regression.py" `
    "$repoRoot\bin\full_regression_round.py"
  if ($LASTEXITCODE -ne 0) {
    throw "py_compile failed with code $LASTEXITCODE"
  }
}

Run-Command "Unit tests" {
  Push-Location $backendDir
  try {
    & $pythonExe -m pytest tests\unit -q
    if ($LASTEXITCODE -ne 0) {
      throw "pytest failed with code $LASTEXITCODE"
    }
  } finally {
    Pop-Location
  }
}

if (-not $SkipE2E) {
  Run-Command "Full regression round" {
    Push-Location $repoRoot
    try {
      & $pythonExe ".\bin\full_regression_round.py"
      if ($LASTEXITCODE -ne 0) {
        throw "full regression failed with code $LASTEXITCODE"
      }
    } finally {
      Pop-Location
    }
  }
} else {
  Write-Step "Skip E2E regression"
  Write-Host "SKIPPED: -SkipE2E enabled"
}

Run-Command "Secret pattern scan (excluding local .env files)" {
  Push-Location $repoRoot
  try {
    $scan = rg -n --no-heading --glob "!.venv/**" --glob "!source/backend/.venv/**" --glob "!source/backend/storage/**" --glob "!source/backend/tmp_*/**" --glob "!.git/**" --glob "!**/.env" "sk-[A-Za-z0-9]{20,}|LLM_API_KEY\\s*=\\s*sk-" .
    if ($LASTEXITCODE -eq 0 -and $scan) {
      throw "Potential secret found:`n$scan"
    }
  } finally {
    Pop-Location
  }
}

Run-Command "Ensure .env files are not tracked by git" {
  if (Test-Path (Join-Path $repoRoot ".git")) {
    Push-Location $repoRoot
    try {
      $trackedEnv = git ls-files -- ".env" "source/backend/.env"
      if ($LASTEXITCODE -ne 0) {
        throw "git ls-files failed"
      }
      if ($trackedEnv) {
        throw "Tracked .env file(s) found:`n$trackedEnv"
      }
    } finally {
      Pop-Location
    }
  } else {
    Write-Host "SKIPPED: git not initialized, cannot verify tracked .env files."
  }
}

Write-Step "Git working tree checks"
if (Test-Path (Join-Path $repoRoot ".git")) {
  Push-Location $repoRoot
  try {
    $status = git status --short
    if ($LASTEXITCODE -ne 0) {
      throw "git status failed"
    }
    if ($status) {
      Write-Host "WARN: working tree not clean, review before push."
      Write-Host $status
    } else {
      Write-Host "PASS: working tree clean" -ForegroundColor Green
    }
  } finally {
    Pop-Location
  }
} else {
  Write-Host "WARN: .git not found at repo root. Initialize git before pushing to remote."
}

Write-Host ""
if ($ok) {
  Write-Host "Pre-release precheck finished: PASS" -ForegroundColor Green
  exit 0
}

Write-Host "Pre-release precheck finished: FAIL" -ForegroundColor Red
exit 2
