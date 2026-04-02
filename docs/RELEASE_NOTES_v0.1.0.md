# BetterPPT v0.1.0 Release Notes

Release date: 2026-04-02

## Summary

v0.1.0 is the first externally deliverable baseline of BetterPPT. It includes runnable backend/worker services, API contracts for tasks/files/metrics, and a minimal frontend integration page.

## Highlights

- FastAPI backend with task lifecycle APIs
- Worker pipeline skeleton with replay and quality-report related capabilities
- File upload/download flow and template asset endpoints
- Metrics overview endpoint
- Unit tests and regression scripts
- Pre-release precheck script

## API Surface (v0.1.0)

- `GET /api/v1/health`
- `POST /api/v1/files/upload-url`
- `PUT /api/v1/files/upload/{file_id}`
- `POST /api/v1/files/complete`
- `GET /api/v1/files/download/{file_id}`
- `POST /api/v1/files/{file_id}/delete`
- `POST /api/v1/tasks`
- `GET /api/v1/tasks`
- `GET /api/v1/tasks/{task_no}`
- `GET /api/v1/tasks/{task_no}/events`
- `GET /api/v1/tasks/{task_no}/replay`
- `GET /api/v1/tasks/{task_no}/quality-report`
- `GET /api/v1/tasks/{task_no}/mappings`
- `POST /api/v1/tasks/{task_no}/retry`
- `POST /api/v1/tasks/{task_no}/cancel`
- `POST /api/v1/tasks/{task_no}/delete`
- `GET /api/v1/tasks/{task_no}/preview`
- `GET /api/v1/tasks/{task_no}/result`
- `GET /api/v1/metrics/overview`
- `POST /api/v1/templates/{file_id}/assetize`
- `GET /api/v1/templates/{file_id}/assets`

## Validation Status

- Unit tests: pass (`37 passed`)
- First release package readiness: in progress (requires secret rotation and sample asset review before public release)

## Breaking / Important Notes

- Development auth mode currently allows default anonymous user mapping (`user_id=1`).
- Local runtime folders (`storage/`, `tmp_*`) are not part of release artifacts.
- Sample files under `ref/` should be reviewed for redistribution rights before public release.

## Upgrade Notes

Not applicable for first public tag.

## Next Version Focus (v0.1.1+)

- Harden authentication and authorization
- Add CI quality gates (lint/security/integration)
- Improve deployment automation and reproducibility
