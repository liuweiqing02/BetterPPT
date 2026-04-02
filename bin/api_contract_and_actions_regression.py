import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def add_check(checklist: list[dict[str, Any]], item: str, passed: bool, evidence: str) -> None:
    checklist.append({'item': item, 'pass': bool(passed), 'evidence': evidence})


def _extract_step_codes(replay_obj: dict[str, Any] | None) -> list[str]:
    steps = (replay_obj or {}).get('data', {}).get('steps', []) or []
    if not isinstance(steps, list):
        return []
    step_codes: list[str] = []
    for step in steps:
        if isinstance(step, dict):
            code = step.get('step_code')
            if code is not None:
                step_codes.append(str(code))
    return step_codes


def _step_codes_in_order(step_codes: list[str], required_codes: list[str]) -> bool:
    cursor = -1
    for code in required_codes:
        try:
            cursor = step_codes.index(code, cursor + 1)
        except ValueError:
            return False
    return True


def wait_http_2xx(url: str, timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url=url, method='GET')
            with urllib.request.urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            time.sleep(0.7)
    return False


def request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 40) -> tuple[int, dict[str, Any]]:
    headers = {'Accept': 'application/json'}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode('utf-8', errors='ignore')
            obj = json.loads(body) if body else {}
            return resp.status, obj if isinstance(obj, dict) else {'data': obj}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='ignore')
        try:
            obj = json.loads(body) if body else {}
            if not isinstance(obj, dict):
                obj = {'data': obj}
        except Exception:
            obj = {'raw': body}
        return exc.code, obj


def request_api(api_base: str, method: str, path: str, payload: dict[str, Any] | None = None, timeout: int = 40) -> tuple[int, dict[str, Any]]:
    return request_json(method=method, url=f'{api_base}{path}', payload=payload, timeout=timeout)


def put_file(upload_url: str, path: Path, content_type: str) -> int:
    data = path.read_bytes()
    req = urllib.request.Request(upload_url, data=data, method='PUT')
    req.add_header('Content-Type', content_type)
    req.add_header('Content-Length', str(len(data)))
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.status


def upload_file_via_api(api_base: str, path: Path, file_role: str, content_type: str) -> int:
    status, slot = request_api(
        api_base,
        'POST',
        '/files/upload-url',
        {
            'filename': path.name,
            'file_role': file_role,
            'content_type': content_type,
            'file_size': path.stat().st_size,
        },
    )
    if status != 200 or slot.get('code') != 0:
        raise RuntimeError(f'upload-url failed status={status} body={slot}')

    file_id = int(slot['data']['file_id'])
    upload_url = str(slot['data']['upload_url'])
    put_status = put_file(upload_url, path, content_type)
    if put_status < 200 or put_status >= 300:
        raise RuntimeError(f'put upload failed status={put_status}')

    status, done = request_api(api_base, 'POST', '/files/complete', {'file_id': file_id, 'checksum_sha256': None})
    if status != 200 or done.get('code') != 0:
        raise RuntimeError(f'complete failed status={status} body={done}')
    return file_id


def create_task(api_base: str, source_file_id: int, reference_file_id: int, detail_level: str, rag_enabled: bool, user_prompt: str) -> str:
    status, resp = request_api(
        api_base,
        'POST',
        '/tasks',
        {
            'source_file_id': source_file_id,
            'reference_file_id': reference_file_id,
            'detail_level': detail_level,
            'user_prompt': user_prompt,
            'rag_enabled': rag_enabled,
            'idempotency_key': f'contract-{int(time.time() * 1000)}',
        },
    )
    if status != 200 or resp.get('code') != 0:
        raise RuntimeError(f'create task failed status={status} body={resp}')
    return str(resp['data']['task_no'])


def wait_task_terminal(api_base: str, task_no: str, timeout_seconds: int = 240) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        status, resp = request_api(api_base, 'GET', f'/tasks/{task_no}')
        if status == 200 and resp.get('code') == 0:
            state = str(resp['data']['status'])
            if state in {'succeeded', 'failed', 'canceled'}:
                return True, state
        time.sleep(2)
    return False, 'timeout'


def start_process(cmd: list[str], cwd: Path, out_path: Path, err_path: Path):
    import subprocess

    out_fp = open(out_path, 'ab')
    err_fp = open(err_path, 'ab')
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=out_fp, stderr=err_fp)
    return proc, out_fp, err_fp


def _resolve_source_pdf(root: Path, source_pdf_arg: str | None) -> Path:
    if source_pdf_arg:
        source_pdf = Path(source_pdf_arg)
        if source_pdf.exists():
            return source_pdf
        raise RuntimeError(f'source pdf not found: {source_pdf}')

    default_pdf = root / 'ref' / 'DynaCollab.pdf'
    if default_pdf.exists():
        return default_pdf

    candidates = sorted((root / 'ref').glob('*.pdf'))
    if not candidates:
        raise RuntimeError('no source pdf found under ref/')
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description='Run API contract + retry/cancel regression checks.')
    parser.add_argument('--api-base', default='http://127.0.0.1:18000/api/v1')
    parser.add_argument('--source-pdf')
    parser.add_argument('--reference-pptx')
    parser.add_argument('--keep-api', action='store_true')
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    source_pdf = _resolve_source_pdf(root, args.source_pdf)
    reference_default = root / 'ref' / 'processed_东南大学PPT-作品91页.pptx'
    if args.reference_pptx:
        reference_pptx = Path(args.reference_pptx)
    elif reference_default.exists():
        reference_pptx = reference_default
    else:
        candidates = sorted((root / 'ref').glob('*.ppt*'))
        if not candidates:
            raise RuntimeError('no reference ppt/pptx found under ref/')
        reference_pptx = candidates[0]
    backend_dir = root / 'source' / 'backend'
    backend_python = backend_dir / '.venv' / 'Scripts' / 'python.exe'
    report_dir = backend_dir / 'tmp_acceptance_runs'
    log_dir = backend_dir / 'tmp_acceptance_logs'
    report_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(backend_dir))
    os.chdir(backend_dir)
    from app.core.constants import TaskStatus
    from app.db.session import SessionLocal
    from app.models.task import Task
    from sqlalchemy import select

    checklist: list[dict[str, Any]] = []
    run_error = None
    api_started = False
    api_proc = None
    api_out = None
    api_err = None
    worker_proc = None
    worker_out = None
    worker_err = None
    task_cancel = None
    task_retry = None
    task_success = None
    source_file_id = None
    reference_file_id = None

    try:
        if not wait_http_2xx(f"{args.api_base}/health", timeout_seconds=3):
            api_proc, api_out, api_err = start_process(
                [str(backend_python), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '18000'],
                backend_dir,
                log_dir / 'contract_api.out.log',
                log_dir / 'contract_api.err.log',
            )
            api_started = True
        ok = wait_http_2xx(f"{args.api_base}/health", timeout_seconds=90)
        add_check(checklist, 'api_health', ok, f'{args.api_base}/health reachable={ok}')
        if not ok:
            raise RuntimeError('api not reachable')

        source_file_id = upload_file_via_api(args.api_base, source_pdf, 'pdf_source', 'application/pdf')
        reference_file_id = upload_file_via_api(
            args.api_base,
            reference_pptx,
            'ppt_reference',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        )
        add_check(checklist, 'upload_ok', source_file_id > 0 and reference_file_id > 0, f'source={source_file_id},ref={reference_file_id}')

        task_cancel = create_task(args.api_base, source_file_id, reference_file_id, 'balanced', True, 'cancel regression')
        status, cancel_resp = request_api(args.api_base, 'POST', f'/tasks/{task_cancel}/cancel')
        canceled_ok = status == 200 and cancel_resp.get('code') == 0 and cancel_resp.get('data', {}).get('status') == TaskStatus.CANCELED
        add_check(checklist, 'cancel_queued_ok', canceled_ok, f'status={status},body={cancel_resp}')

        status, cancel_again = request_api(args.api_base, 'POST', f'/tasks/{task_cancel}/cancel')
        cancel_again_ok = status == 409 and cancel_again.get('code') == 1004
        add_check(checklist, 'cancel_again_conflict', cancel_again_ok, f'status={status},body={cancel_again}')

        task_retry = create_task(args.api_base, source_file_id, reference_file_id, 'concise', False, 'retry regression')
        with SessionLocal() as db:
            task = db.scalar(select(Task).where(Task.task_no == task_retry))
            if not task:
                raise RuntimeError('retry task missing in db')
            task.status = TaskStatus.FAILED
            task.retry_count = 0
            task.error_code = '2001'
            task.error_message = 'forced for retry regression'
            db.commit()

        status, retry_resp = request_api(args.api_base, 'POST', f'/tasks/{task_retry}/retry')
        retry_ok = status == 200 and retry_resp.get('code') == 0 and retry_resp.get('data', {}).get('status') == TaskStatus.QUEUED
        add_check(checklist, 'retry_failed_ok', retry_ok, f'status={status},body={retry_resp}')

        with SessionLocal() as db:
            task = db.scalar(select(Task).where(Task.task_no == task_retry))
            if not task:
                raise RuntimeError('retry task missing in db second update')
            task.status = TaskStatus.FAILED
            task.retry_count = 3
            task.error_code = '2001'
            task.error_message = 'forced retry limit'
            db.commit()

        status, retry_limit = request_api(args.api_base, 'POST', f'/tasks/{task_retry}/retry')
        retry_limit_ok = status == 409 and retry_limit.get('code') == 1004
        add_check(checklist, 'retry_limit_conflict', retry_limit_ok, f'status={status},body={retry_limit}')

        task_success = create_task(args.api_base, source_file_id, reference_file_id, 'balanced', True, 'contract success run')
        worker_proc, worker_out, worker_err = start_process(
            [str(backend_python), '-m', 'app.workers.runner'],
            backend_dir,
            log_dir / 'contract_worker.out.log',
            log_dir / 'contract_worker.err.log',
        )
        finished, terminal = wait_task_terminal(args.api_base, task_success, timeout_seconds=360)
        add_check(checklist, 'worker_success_task', finished and terminal == 'succeeded', f'finished={finished},terminal={terminal}')

        contract_paths = [
            ('task_detail', 'GET', f'/tasks/{task_success}'),
            ('task_events', 'GET', f'/tasks/{task_success}/events?limit=20'),
            ('task_replay', 'GET', f'/tasks/{task_success}/replay?limit=100'),
            ('task_quality_report', 'GET', f'/tasks/{task_success}/quality-report'),
            ('task_mappings', 'GET', f'/tasks/{task_success}/mappings?attempt_no=latest&limit=100'),
            ('task_preview', 'GET', f'/tasks/{task_success}/preview'),
            ('task_result', 'GET', f'/tasks/{task_success}/result'),
            ('task_list', 'GET', '/tasks?limit=20'),
            ('metrics', 'GET', '/metrics/overview?days=7'),
        ]
        replay_obj = None
        for key, method, path in contract_paths:
            status, resp = request_api(args.api_base, method, path)
            ok_item = status == 200 and resp.get('code') == 0 and isinstance(resp.get('data'), dict)
            add_check(checklist, f'contract_{key}', ok_item, f'status={status}')
            if key == 'task_replay' and ok_item:
                replay_obj = resp

        mapping_ok = False
        analysis_source_ok = False
        analysis_source_value = ''
        replay_step_codes: list[str] = []
        if replay_obj:
            steps = replay_obj.get('data', {}).get('steps', []) or []
            replay_step_codes = _extract_step_codes(replay_obj)
            required_chain = [
                'parse_pdf',
                'analyze_template',
                'assetize_template',
                'plan_slides',
                'map_slots',
                'generate_slides',
                'self_correct',
                'export_ppt',
            ]
            chain_ok = _step_codes_in_order(replay_step_codes, required_chain)
            add_check(checklist, 'contract_replay_chain_v12', chain_ok, f'steps={replay_step_codes}')

            for step in steps:
                if step.get('step_code') == 'analyze_template':
                    output_json = step.get('output_json') or {}
                    analysis_source_value = str(output_json.get('analysis_source') or '')
                    analysis_source_ok = analysis_source_value == 'template_service_persisted'
                    break

            for step in steps:
                if step.get('step_code') == 'generate_slides':
                    output_json = step.get('output_json') or {}
                    mapping_ok = bool(output_json.get('mapping_mode')) and 'mapped_slide_plan' in output_json
                    break
        add_check(checklist, 'contract_generate_mapping_fields', mapping_ok, f'mapping_fields_present={mapping_ok}')
        add_check(
            checklist,
            'contract_analyze_template_source',
            analysis_source_ok,
            f'analysis_source={analysis_source_value or "<missing>"}',
        )

        quality_report_ok = False
        quality_report_summary = ''
        status, quality_report_resp = request_api(args.api_base, 'GET', f'/tasks/{task_success}/quality-report')
        if status == 200 and quality_report_resp.get('code') == 0:
            data = quality_report_resp.get('data') or {}
            quality_report_ok = (
                isinstance(data, dict)
                and data.get('task_no') == task_success
                and 'metric_version' in data
                and 'evaluated_pages' in data
                and 'pass_flag' in data
            )
            quality_report_summary = (
                f"task_no={data.get('task_no')},metric_version={data.get('metric_version')},"
                f"evaluated_pages={data.get('evaluated_pages')},pass_flag={data.get('pass_flag')}"
            )
        add_check(checklist, 'contract_quality_report_structure', quality_report_ok, quality_report_summary or f'status={status},body={quality_report_resp}')

        mappings_ok = False
        mappings_summary = ''
        status, mappings_resp = request_api(args.api_base, 'GET', f'/tasks/{task_success}/mappings?attempt_no=latest&limit=100')
        if status == 200 and mappings_resp.get('code') == 0:
            data = mappings_resp.get('data') or {}
            items = data.get('items')
            items_ok = isinstance(items, list)
            first_item = items[0] if items_ok and items else {}
            first_slot_fillings = first_item.get('slot_fillings') if isinstance(first_item, dict) else None
            mappings_ok = (
                isinstance(data, dict)
                and data.get('task_no') == task_success
                and ('attempt_no' in data)
                and items_ok
                and 'next_cursor' in data
                and (first_item == {} or (
                    isinstance(first_item, dict)
                    and 'slide_no' in first_item
                    and 'template_page_no' in first_item
                    and 'fallback_level' in first_item
                    and isinstance(first_slot_fillings, list)
                ))
            )
            mappings_summary = (
                f"task_no={data.get('task_no')},attempt_no={data.get('attempt_no')},"
                f"items={len(items) if isinstance(items, list) else 'n/a'},next_cursor={data.get('next_cursor')}"
            )
        add_check(checklist, 'contract_mappings_structure', mappings_ok, mappings_summary or f'status={status},body={mappings_resp}')

    except Exception as exc:
        run_error = str(exc)
    finally:
        for fp in [worker_out, worker_err]:
            try:
                if fp:
                    fp.close()
            except Exception:
                pass
        if worker_proc is not None and worker_proc.poll() is None:
            worker_proc.terminate()
            time.sleep(0.5)
            if worker_proc.poll() is None:
                worker_proc.kill()

        if not args.keep_api and api_started and api_proc is not None and api_proc.poll() is None:
            api_proc.terminate()
            time.sleep(0.5)
            if api_proc.poll() is None:
                api_proc.kill()
        for fp in [api_out, api_err]:
            try:
                if fp:
                    fp.close()
            except Exception:
                pass

    all_passed = bool(checklist) and all(item['pass'] for item in checklist) and not run_error
    report = {
        'run_at': now_iso(),
        'api_base': args.api_base,
        'source_pdf': str(source_pdf),
        'reference_pptx': str(reference_pptx),
        'task_cancel': task_cancel,
        'task_retry': task_retry,
        'task_success': task_success,
        'source_file_id': source_file_id,
        'reference_file_id': reference_file_id,
        'all_passed': all_passed,
        'error': run_error,
        'checklist': checklist,
    }

    report_path = report_dir / f'contract_actions_regression_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    print(f'report_path={report_path}')
    print(f'all_passed={all_passed}')
    if run_error:
        print(f'error={run_error}')
    for item in checklist:
        print(f"check: {item['item']} | pass={item['pass']} | evidence={item['evidence']}")

    return 0 if all_passed else 2


if __name__ == '__main__':
    raise SystemExit(main())

