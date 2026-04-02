param(
  [string]$ApiBase = "http://127.0.0.1:8000/api/v1",
  [string]$FrontBase = "http://127.0.0.1:5173",
  [string]$SourcePdf = "",
  [string]$ReferencePptx = "",
  [int]$PollTimeoutSeconds = 360,
  [switch]$KeepServices
)

$ErrorActionPreference = "Stop"

function Add-Check {
  param(
    [System.Collections.Generic.List[object]]$Checklist,
    [string]$Item,
    [bool]$Pass,
    [string]$Evidence
  )
  $Checklist.Add([PSCustomObject]@{
      item = $Item
      pass = $Pass
      evidence = $Evidence
    })
}

function Invoke-Json {
  param(
    [string]$Method,
    [string]$Url,
    [object]$Body = $null
  )
  if ($null -eq $Body) {
    return Invoke-RestMethod -Method $Method -Uri $Url -TimeoutSec 40
  }
  $json = $Body | ConvertTo-Json -Depth 20 -Compress
  return Invoke-RestMethod -Method $Method -Uri $Url -TimeoutSec 40 -ContentType "application/json; charset=utf-8" -Body $json
}

function Wait-Http200 {
  param(
    [string]$Url,
    [int]$TimeoutSeconds = 60
  )
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
      if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) {
        return $true
      }
    }
    catch {
      Start-Sleep -Milliseconds 700
      continue
    }
  }
  return $false
}

function Upload-FileViaApi {
  param(
    [string]$ApiBase,
    [string]$Path,
    [string]$FileRole,
    [string]$ContentType
  )

  if (-not (Test-Path $Path)) {
    throw "file not found: $Path"
  }

  $file = Get-Item -LiteralPath $Path
  $slotResp = Invoke-Json -Method "POST" -Url "$ApiBase/files/upload-url" -Body @{
    filename = $file.Name
    file_role = $FileRole
    content_type = $ContentType
    file_size = [int64]$file.Length
  }

  $fileId = [int]$slotResp.data.file_id
  $uploadUrl = [string]$slotResp.data.upload_url

  $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
  if ($null -eq $curl) {
    throw "curl.exe not found in PATH"
  }

  & curl.exe --fail --silent --show-error -X PUT -H "Content-Type: $ContentType" --upload-file "$Path" "$uploadUrl" | Out-Null

  Invoke-Json -Method "POST" -Url "$ApiBase/files/complete" -Body @{
    file_id = $fileId
    checksum_sha256 = $null
  } | Out-Null

  return $fileId
}

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
if ([string]::IsNullOrWhiteSpace($ReferencePptx)) {
  $defaultReferencePptx = Join-Path $refDir "processed_东南大学PPT-作品91页.pptx"
  if (Test-Path $defaultReferencePptx) {
    $ReferencePptx = $defaultReferencePptx
  } else {
    $pptCandidates = Get-ChildItem -Path $refDir -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @(".ppt", ".pptx") } | Sort-Object Name
    if ($pptCandidates.Count -gt 0) {
      $ReferencePptx = $pptCandidates[0].FullName
    } else {
      throw "No reference ppt/pptx found under $refDir. Please pass -ReferencePptx explicitly."
    }
  }
}
$backendDir = Join-Path $root "source\backend"
$frontendDir = Join-Path $root "source\frontend"
$backendPython = Join-Path $backendDir ".venv\Scripts\python.exe"

if (-not (Test-Path $backendPython)) {
  throw "backend python not found: $backendPython"
}

$logDir = Join-Path $backendDir "tmp_acceptance_logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$apiOutLog = Join-Path $logDir "api.out.log"
$apiErrLog = Join-Path $logDir "api.err.log"
$workerOutLog = Join-Path $logDir "worker.out.log"
$workerErrLog = Join-Path $logDir "worker.err.log"
$frontOutLog = Join-Path $logDir "frontend.out.log"
$frontErrLog = Join-Path $logDir "frontend.err.log"

$checklist = New-Object "System.Collections.Generic.List[object]"
$apiStarted = $false
$workerStarted = $false
$frontStarted = $false
$apiProc = $null
$workerProc = $null
$frontProc = $null
$taskNo = $null
$sourceFileId = $null
$referenceFileId = $null
$runError = $null

try {
  if (-not (Wait-Http200 -Url "$ApiBase/health" -TimeoutSeconds 3)) {
    $apiProc = Start-Process -FilePath $backendPython -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -PassThru -WorkingDirectory $backendDir -RedirectStandardOutput $apiOutLog -RedirectStandardError $apiErrLog
    $apiStarted = $true
  }
  if (Wait-Http200 -Url "$ApiBase/health" -TimeoutSeconds 80) {
    Add-Check -Checklist $checklist -Item "api_health" -Pass $true -Evidence "$ApiBase/health is reachable"
  }
  else {
    Add-Check -Checklist $checklist -Item "api_health" -Pass $false -Evidence "$ApiBase/health is not reachable"
    throw "api health check failed"
  }

  if (-not (Wait-Http200 -Url "$FrontBase/" -TimeoutSeconds 3)) {
    $frontProc = Start-Process -FilePath "python" -ArgumentList @("-m", "http.server", "5173") -PassThru -WorkingDirectory $frontendDir -RedirectStandardOutput $frontOutLog -RedirectStandardError $frontErrLog
    $frontStarted = $true
  }
  if (Wait-Http200 -Url "$FrontBase/" -TimeoutSeconds 40) {
    Add-Check -Checklist $checklist -Item "frontend_reachable" -Pass $true -Evidence "$FrontBase is reachable"
  }
  else {
    Add-Check -Checklist $checklist -Item "frontend_reachable" -Pass $false -Evidence "$FrontBase is not reachable"
    throw "frontend check failed"
  }

  $workerProc = Start-Process -FilePath $backendPython -ArgumentList @("-m", "app.workers.runner") -PassThru -WorkingDirectory $backendDir -RedirectStandardOutput $workerOutLog -RedirectStandardError $workerErrLog
  Start-Sleep -Seconds 2
  if ($workerProc.HasExited) {
    Add-Check -Checklist $checklist -Item "worker_started" -Pass $false -Evidence "worker exited immediately; see $workerErrLog"
    throw "worker startup failed"
  }
  $workerStarted = $true
  Add-Check -Checklist $checklist -Item "worker_started" -Pass $true -Evidence "worker pid=$($workerProc.Id)"

  $indexHtml = (Invoke-WebRequest -Uri "$FrontBase/index.html" -UseBasicParsing -TimeoutSec 30).Content
  $hasReplay = ($indexHtml -match 'id="replayPanel"') -and ($indexHtml -match 'id="replayRefreshBtn"') -and ($indexHtml -match 'id="replayStatus"')
  Add-Check -Checklist $checklist -Item "ui_replay_section" -Pass $hasReplay -Evidence "replayPanel/replayRefreshBtn/replayStatus present=$hasReplay"
  $hasMetrics = ($indexHtml -match 'id="metricsPanel"') -and ($indexHtml -match 'id="metricsRefreshBtn"') -and ($indexHtml -match 'id="metricsStatus"') -and ($indexHtml -match 'id="metricsDaysInput"')
  Add-Check -Checklist $checklist -Item "ui_metrics_section" -Pass $hasMetrics -Evidence "metricsPanel/metricsRefreshBtn/metricsStatus/metricsDaysInput present=$hasMetrics"
  $hasTaskNoInput = $indexHtml -match 'id="taskNoInput"'
  Add-Check -Checklist $checklist -Item "ui_task_no_input" -Pass $hasTaskNoInput -Evidence "taskNoInput present=$hasTaskNoInput"

  $sourceFileId = Upload-FileViaApi -ApiBase $ApiBase -Path $SourcePdf -FileRole "pdf_source" -ContentType "application/pdf"
  Add-Check -Checklist $checklist -Item "upload_source_pdf" -Pass ($sourceFileId -gt 0) -Evidence "source_file_id=$sourceFileId"

  $referenceFileId = Upload-FileViaApi -ApiBase $ApiBase -Path $ReferencePptx -FileRole "ppt_reference" -ContentType "application/vnd.openxmlformats-officedocument.presentationml.presentation"
  Add-Check -Checklist $checklist -Item "upload_reference_pptx" -Pass ($referenceFileId -gt 0) -Evidence "reference_file_id=$referenceFileId"

  $createResp = Invoke-Json -Method "POST" -Url "$ApiBase/tasks" -Body @{
    source_file_id = $sourceFileId
    reference_file_id = $referenceFileId
    detail_level = "balanced"
    user_prompt = "page acceptance round"
    rag_enabled = $true
    idempotency_key = "acceptance-$(Get-Date -Format yyyyMMddHHmmssfff)"
  }
  $taskNo = [string]$createResp.data.task_no
  Add-Check -Checklist $checklist -Item "task_created" -Pass ([string]::IsNullOrWhiteSpace($taskNo) -eq $false) -Evidence "task_no=$taskNo"

  $deadline = (Get-Date).AddSeconds($PollTimeoutSeconds)
  $finalStatus = ""
  while ((Get-Date) -lt $deadline) {
    $detail = Invoke-Json -Method "GET" -Url "$ApiBase/tasks/$taskNo"
    $status = [string]$detail.data.status
    if ($status -in @("succeeded", "failed", "canceled")) {
      $finalStatus = $status
      break
    }
    Start-Sleep -Seconds 2
  }
  if ([string]::IsNullOrWhiteSpace($finalStatus)) {
    Add-Check -Checklist $checklist -Item "task_final_status" -Pass $false -Evidence "timed out after $PollTimeoutSeconds seconds"
    throw "task poll timed out"
  }
  Add-Check -Checklist $checklist -Item "task_final_status" -Pass ($finalStatus -eq "succeeded") -Evidence "status=$finalStatus"

  $replayResp = Invoke-Json -Method "GET" -Url "$ApiBase/tasks/$taskNo/replay?limit=100"
  $steps = @($replayResp.data.steps)
  $events = @($replayResp.data.events)
  Add-Check -Checklist $checklist -Item "replay_available" -Pass (($steps.Count -gt 0) -and ($events.Count -gt 0)) -Evidence "steps=$($steps.Count), events=$($events.Count)"
  $stepCodes = @($steps | ForEach-Object { [string]$_.step_code })
  $hasKeySteps = ($stepCodes -contains "rag_retrieve") -and ($stepCodes -contains "self_correct")
  Add-Check -Checklist $checklist -Item "replay_key_steps" -Pass $hasKeySteps -Evidence "step_codes=$($stepCodes -join ',')"

  $previewResp = Invoke-Json -Method "GET" -Url "$ApiBase/tasks/$taskNo/preview"
  $slides = @($previewResp.data.slides)
  $previewSource = [string]$previewResp.data.preview_source
  Add-Check -Checklist $checklist -Item "preview_available" -Pass ($slides.Count -gt 0) -Evidence "slides=$($slides.Count), source=$previewSource"

  $resultResp = Invoke-Json -Method "GET" -Url "$ApiBase/tasks/$taskNo/result"
  $downloadUrl = [string]$resultResp.data.download_url
  Add-Check -Checklist $checklist -Item "result_download_url" -Pass ([string]::IsNullOrWhiteSpace($downloadUrl) -eq $false) -Evidence $downloadUrl

  $metricsResp = Invoke-Json -Method "GET" -Url "$ApiBase/metrics/overview?days=7"
  $totalTasks = [int]$metricsResp.data.total_tasks
  $successTasks = [int]$metricsResp.data.success_tasks
  Add-Check -Checklist $checklist -Item "metrics_available" -Pass ($totalTasks -ge 1) -Evidence "total=$totalTasks, success=$successTasks"
}
catch {
  $runError = $_.Exception.Message
}
finally {
  if (-not $KeepServices) {
    if ($workerStarted -and $workerProc -and (-not $workerProc.HasExited)) {
      Stop-Process -Id $workerProc.Id -Force
    }
    if ($apiStarted -and $apiProc -and (-not $apiProc.HasExited)) {
      Stop-Process -Id $apiProc.Id -Force
    }
    if ($frontStarted -and $frontProc -and (-not $frontProc.HasExited)) {
      Stop-Process -Id $frontProc.Id -Force
    }
  }
}

$allPassed = (($checklist | Where-Object { $_.pass -eq $false }).Count -eq 0) -and [string]::IsNullOrWhiteSpace($runError)
$runAt = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"

$report = [PSCustomObject]@{
  run_at = $runAt
  api_base = $ApiBase
  front_base = $FrontBase
  source_pdf = $SourcePdf
  reference_pptx = $ReferencePptx
  task_no = $taskNo
  source_file_id = $sourceFileId
  reference_file_id = $referenceFileId
  all_passed = $allPassed
  error = $runError
  checklist = $checklist
}

$reportDir = Join-Path $backendDir "tmp_acceptance_runs"
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null
$reportPath = Join-Path $reportDir ("page_acceptance_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".json")
$report | ConvertTo-Json -Depth 30 | Set-Content -Path $reportPath -Encoding UTF8

Write-Output ("report_path=" + $reportPath)
Write-Output ("task_no=" + $taskNo)
Write-Output ("all_passed=" + $allPassed)
if ($runError) {
  Write-Output ("error=" + $runError)
}
$checklist | ForEach-Object {
  Write-Output ("check: " + $_.item + " | pass=" + $_.pass + " | evidence=" + $_.evidence)
}

if (-not $allPassed) {
  exit 2
}
