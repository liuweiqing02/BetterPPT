# BetterPPT Pre-Upload Checklist

This checklist is for final preparation before pushing to GitHub.

## 1) Run Local Validation

From repo root:

```powershell
.\bin\pre_release_precheck.ps1
```

Quick mode:

```powershell
.\bin\pre_release_precheck.ps1 -SkipE2E
```

## 2) Security Gate (Must Pass)

- Confirm no real keys are present in files to be committed.
- Confirm `source/backend/.env` is not staged.
- Rotate leaked keys before public release if any leak occurred.
- Confirm no personal path, private URL, private account data in committed JSON/log files.

## 3) Runtime Artifact Gate (Must Pass)

Do not include these in commit:

- `source/backend/storage/`
- `source/backend/tmp_*/`
- `source/backend/betterppt_*.db`
- `.venv/`, `source/backend/.venv/`
- `.pytest_cache/`
- `*.pyc`, `__pycache__/`

## 4) Sample Data Gate

- Review `ref/` assets for redistribution rights.
- For public repo, replace private assets with redistributable demo assets.

## 5) Migration and API Contract Gate

Required migration files:

```text
source/backend/migrations/001_init.sql
source/backend/migrations/002_v12_create_template_slot_definitions.sql
source/backend/migrations/003_v12_create_task_mapping_and_filling_tables.sql
source/backend/migrations/004_v12_create_task_quality_reports.sql
source/backend/migrations/005_v12_alter_tasks_and_files.sql
source/backend/migrations/006_v12_task_steps_attempt_expand.sql
source/backend/migrations/007_v12_task_steps_drop_legacy_unique_contract.sql
```

## 6) Git Push Flow

If `.git` is not initialized:

```powershell
git init
git add .
git commit -m "chore: bootstrap repository for v0.1.0"
```

Then connect remote and push:

```powershell
git branch -M main
git remote add origin <your_repo_url>
git push -u origin main
```

## 7) Release Tag

```powershell
git tag -a v0.1.0 -m "BetterPPT first public deliverable"
git push origin v0.1.0
```

## 8) Final Manual Spot Check

- API starts successfully.
- Worker starts successfully.
- Frontend can load and operate basic flow.
- One full task can be created, completed, previewed, and downloaded.
