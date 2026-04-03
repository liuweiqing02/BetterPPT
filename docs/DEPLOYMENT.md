# Deployment Guide

## Scope

This document describes practical deployment options for BetterPPT v0.1.2.

## Mode A: Internal Repository / Internal Environment (Recommended First)

Use this mode when:
- source documents and templates are internal assets
- business data is sensitive
- external contributors are not yet involved

### Recommended setup

- Network: intranet only
- Backend: 1 FastAPI instance + 1 worker instance
- Database: MySQL 8
- Queue/Cache: Redis
- Storage: local disk or private object storage
- Secrets: managed by CI/CD secret store or host-level secret manager

### Deployment steps

1. Prepare machine and runtime.

```powershell
python --version
```

2. Configure env file from `.env.example` and keep it out of VCS.

For stable task latency in release environment, tune:
- `LLM_REQUEST_TIMEOUT_SECONDS` (recommended `10-20`)
- `LLM_REQUEST_MAX_RETRIES` (recommended `0-1`)
- `TASK_CONCURRENCY_PER_USER` (recommended `3`)
- `TASK_CONCURRENCY_ACTIVE_WINDOW_MINUTES` (recommended `120`)
- `UPLOAD_PDF_MAX_FILE_SIZE_MB` / `UPLOAD_REFERENCE_PPT_MAX_FILE_SIZE_MB`

Optional for V1.2 template vision embedding (offline/local model):

```powershell
cd <repo-root>
source\backend\.venv\Scripts\python.exe bin\setup_local_vision_model.py
```

3. Initialize DB schema.

Option A: start API once with auto table create.

Option B: run SQL migrations under `source/backend/migrations/` manually.

4. Start services.

```powershell
# API
cd source/backend
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Worker
cd source/backend
.\.venv\Scripts\python -m app.workers.runner
```

5. Start frontend static server (MVP UI).

```powershell
cd source/frontend
python -m http.server 5173
```

Routing convention in v0.1.2:
- frontend views: `/app/*` (or `index.html#/app/*` in static mode)
- backend API: `/api/v1/*`
- legacy debug page: `/legacy.html`

6. Run smoke tests.

```powershell
.\bin\pre_release_precheck.ps1 -SkipE2E
```

For formal release gate, run without `-SkipE2E`:

```powershell
.\bin\pre_release_precheck.ps1
```

## Mode B: Public Open-Source Repository

Use this mode when:
- legal approval for open source is completed
- sample files are redistributable
- internal secrets and private paths are removed

### Extra requirements for public release

- remove all real keys from local `.env`
- replace private sample files under `ref/` with redistributable demo assets
- ensure docs do not include private endpoint or account details
- enforce branch protections and required checks in GitHub

## CI/CD Baseline

Current workflow:
- `.github/workflows/backend-unit-tests.yml` runs backend unit tests on push/PR

Recommended additions:
- lint step (`ruff`, `black --check`) [待补充]
- security scan (`pip-audit`, secret scan) [待补充]
- integration test job with MySQL + Redis service containers [待补充]

## Operations Checklist

- log rotation policy [待补充]
- backup and restore strategy for MySQL [待补充]
- retention policy for generated files in `storage/` [待补充]
- monitoring and alerting (error rate, queue lag, task latency) [待补充]

## Rollback Suggestions

- Keep previous application package for quick rollback.
- Use immutable version tags (`v0.1.0`, `v0.1.1`, `v0.1.2`) and do not rewrite tags.
- For schema changes, keep backward-compatible migration windows where possible.
