import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_check(checklist, item: str, passed: bool, evidence: str) -> None:
    checklist.append({"item": item, "pass": bool(passed), "evidence": evidence})


def wait_http_2xx(url: str, timeout_seconds: int = 60) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url=url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    return True
        except Exception:
            time.sleep(0.7)
    return False


def request_json(method: str, url: str, payload=None, timeout: int = 40):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url=url, data=data, headers=headers, method=method.upper())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw)


def put_file(url: str, path: Path, content_type: str, timeout: int = 120):
    data = path.read_bytes()
    req = urllib.request.Request(url=url, data=data, method="PUT")
    req.add_header("Content-Type", content_type)
    req.add_header("Content-Length", str(len(data)))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if not (200 <= resp.status < 300):
            raise RuntimeError(f"PUT failed status={resp.status}")


def upload_file_via_api(api_base: str, path: Path, file_role: str, content_type: str) -> int:
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")

    slot = request_json(
        "POST",
        f"{api_base}/files/upload-url",
        {
            "filename": path.name,
            "file_role": file_role,
            "content_type": content_type,
            "file_size": path.stat().st_size,
        },
    )
    file_id = int(slot["data"]["file_id"])
    upload_url = str(slot["data"]["upload_url"])

    put_file(upload_url, path, content_type)

    request_json(
        "POST",
        f"{api_base}/files/complete",
        {"file_id": file_id, "checksum_sha256": None},
    )
    return file_id


def start_process(cmd, cwd: Path, out_log: Path, err_log: Path):
    out_fp = open(out_log, "ab")
    err_fp = open(err_log, "ab")
    proc = subprocess.Popen(cmd, cwd=str(cwd), stdout=out_fp, stderr=err_fp)
    return proc, out_fp, err_fp


def _resolve_source_pdf(root: Path, source_pdf_arg: str | None) -> Path:
    if source_pdf_arg:
        source_pdf = Path(source_pdf_arg)
        if source_pdf.exists():
            return source_pdf
        raise RuntimeError(f"source pdf not found: {source_pdf}")

    default_pdf = root / "ref" / "DynaCollab.pdf"
    if default_pdf.exists():
        return default_pdf

    candidates = sorted((root / "ref").glob("*.pdf"))
    if not candidates:
        raise RuntimeError("no source pdf found under ref/")
    return candidates[0]


def _resolve_reference_pptx(root: Path, reference_arg: str | None) -> Path:
    if reference_arg:
        reference = Path(reference_arg)
        if reference.exists():
            return reference
        raise RuntimeError(f"reference pptx not found: {reference}")

    default_reference = root / "ref" / "processed_东南大学PPT-作品91页.pptx"
    if default_reference.exists():
        return default_reference

    candidates = sorted((root / "ref").glob("*.ppt*"))
    if not candidates:
        raise RuntimeError("no reference ppt/pptx found under ref/")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one page-level acceptance round.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--front-base", default="http://127.0.0.1:5173")
    parser.add_argument("--source-pdf")
    parser.add_argument("--reference-pptx")
    parser.add_argument("--poll-timeout-seconds", type=int, default=360)
    parser.add_argument("--keep-services", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    backend_dir = root / "source" / "backend"
    frontend_dir = root / "source" / "frontend"
    backend_python = backend_dir / ".venv" / "Scripts" / "python.exe"
    if not backend_python.exists():
        raise RuntimeError(f"backend python not found: {backend_python}")

    source_pdf = _resolve_source_pdf(root, args.source_pdf)
    reference_pptx = _resolve_reference_pptx(root, args.reference_pptx)

    log_dir = backend_dir / "tmp_acceptance_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    report_dir = backend_dir / "tmp_acceptance_runs"
    report_dir.mkdir(parents=True, exist_ok=True)

    checklist = []
    task_no = None
    source_file_id = None
    reference_file_id = None
    run_error = None

    api_started = False
    front_started = False
    worker_started = False

    api_proc = None
    front_proc = None
    worker_proc = None

    proc_files = []

    try:
        if not wait_http_2xx(f"{args.api_base}/health", timeout_seconds=3):
            api_proc, api_out, api_err = start_process(
                [
                    str(backend_python),
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "8000",
                ],
                backend_dir,
                log_dir / "api.out.log",
                log_dir / "api.err.log",
            )
            proc_files.extend([api_out, api_err])
            api_started = True

        if wait_http_2xx(f"{args.api_base}/health", timeout_seconds=80):
            add_check(checklist, "api_health", True, f"{args.api_base}/health is reachable")
        else:
            add_check(checklist, "api_health", False, f"{args.api_base}/health is not reachable")
            raise RuntimeError("api health check failed")

        if not wait_http_2xx(f"{args.front_base}/", timeout_seconds=3):
            front_proc, front_out, front_err = start_process(
                [sys.executable, "-m", "http.server", "5173"],
                frontend_dir,
                log_dir / "frontend.out.log",
                log_dir / "frontend.err.log",
            )
            proc_files.extend([front_out, front_err])
            front_started = True

        if wait_http_2xx(f"{args.front_base}/", timeout_seconds=40):
            add_check(checklist, "frontend_reachable", True, f"{args.front_base} is reachable")
        else:
            add_check(checklist, "frontend_reachable", False, f"{args.front_base} is not reachable")
            raise RuntimeError("frontend check failed")

        worker_proc, worker_out, worker_err = start_process(
            [str(backend_python), "-m", "app.workers.runner"],
            backend_dir,
            log_dir / "worker.out.log",
            log_dir / "worker.err.log",
        )
        proc_files.extend([worker_out, worker_err])
        time.sleep(2)
        if worker_proc.poll() is not None:
            add_check(checklist, "worker_started", False, "worker exited immediately; see worker.err.log")
            raise RuntimeError("worker startup failed")
        worker_started = True
        add_check(checklist, "worker_started", True, f"worker pid={worker_proc.pid}")

        with urllib.request.urlopen(f"{args.front_base}/index.html", timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        has_replay = all(k in html for k in ["id=\"replayPanel\"", "id=\"replayRefreshBtn\"", "id=\"replayStatus\""])
        add_check(checklist, "ui_replay_section", has_replay, f"replay ids present={has_replay}")

        has_metrics = all(k in html for k in ["id=\"metricsPanel\"", "id=\"metricsRefreshBtn\"", "id=\"metricsStatus\"", "id=\"metricsDaysInput\""])
        add_check(checklist, "ui_metrics_section", has_metrics, f"metrics ids present={has_metrics}")

        has_task_no = "id=\"taskNoInput\"" in html
        add_check(checklist, "ui_task_no_input", has_task_no, f"taskNoInput present={has_task_no}")

        source_file_id = upload_file_via_api(args.api_base, source_pdf, "pdf_source", "application/pdf")
        add_check(checklist, "upload_source_pdf", source_file_id > 0, f"source_file_id={source_file_id}")

        reference_file_id = upload_file_via_api(
            args.api_base,
            reference_pptx,
            "ppt_reference",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        add_check(checklist, "upload_reference_pptx", reference_file_id > 0, f"reference_file_id={reference_file_id}")

        create = request_json(
            "POST",
            f"{args.api_base}/tasks",
            {
                "source_file_id": source_file_id,
                "reference_file_id": reference_file_id,
                "detail_level": "balanced",
                "user_prompt": "page acceptance round",
                "rag_enabled": True,
                "idempotency_key": f"acceptance-{int(time.time() * 1000)}",
            },
        )
        task_no = str(create["data"]["task_no"])
        add_check(checklist, "task_created", bool(task_no), f"task_no={task_no}")

        deadline = time.time() + args.poll_timeout_seconds
        final_status = ""
        while time.time() < deadline:
            detail = request_json("GET", f"{args.api_base}/tasks/{task_no}")
            status = str(detail["data"]["status"])
            if status in {"succeeded", "failed", "canceled"}:
                final_status = status
                break
            time.sleep(2)

        if not final_status:
            add_check(checklist, "task_final_status", False, f"timed out after {args.poll_timeout_seconds} seconds")
            raise RuntimeError("task poll timed out")
        add_check(checklist, "task_final_status", final_status == "succeeded", f"status={final_status}")

        replay = request_json("GET", f"{args.api_base}/tasks/{task_no}/replay?limit=100")
        steps = replay.get("data", {}).get("steps", []) or []
        events = replay.get("data", {}).get("events", []) or []
        add_check(checklist, "replay_available", len(steps) > 0 and len(events) > 0, f"steps={len(steps)}, events={len(events)}")

        step_codes = [str(s.get("step_code", "")) for s in steps]
        has_key_steps = ("rag_retrieve" in step_codes) and ("self_correct" in step_codes)
        add_check(checklist, "replay_key_steps", has_key_steps, "step_codes=" + ",".join(step_codes))

        preview = request_json("GET", f"{args.api_base}/tasks/{task_no}/preview")
        slides = preview.get("data", {}).get("slides", []) or []
        preview_source = str(preview.get("data", {}).get("preview_source", ""))
        add_check(checklist, "preview_available", len(slides) > 0, f"slides={len(slides)}, source={preview_source}")

        result = request_json("GET", f"{args.api_base}/tasks/{task_no}/result")
        download_url = str(result.get("data", {}).get("download_url", ""))
        add_check(checklist, "result_download_url", bool(download_url), download_url)

        metrics = request_json("GET", f"{args.api_base}/metrics/overview?days=7")
        total = int(metrics.get("data", {}).get("total_tasks", 0))
        success = int(metrics.get("data", {}).get("success_tasks", 0))
        add_check(checklist, "metrics_available", total >= 1, f"total={total}, success={success}")

    except Exception as exc:
        run_error = str(exc)

    finally:
        if not args.keep_services:
            for proc in [worker_proc, api_proc, front_proc]:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
            time.sleep(0.8)
            for proc in [worker_proc, api_proc, front_proc]:
                if proc is not None and proc.poll() is None:
                    proc.kill()
        for fp in proc_files:
            try:
                fp.close()
            except Exception:
                pass

    all_passed = (all(item["pass"] for item in checklist) if checklist else False) and (not run_error)

    report = {
        "run_at": now_iso(),
        "api_base": args.api_base,
        "front_base": args.front_base,
        "source_pdf": str(source_pdf),
        "reference_pptx": str(reference_pptx),
        "task_no": task_no,
        "source_file_id": source_file_id,
        "reference_file_id": reference_file_id,
        "all_passed": all_passed,
        "error": run_error,
        "checklist": checklist,
    }

    report_path = report_dir / f"page_acceptance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"report_path={report_path}")
    print(f"task_no={task_no}")
    print(f"all_passed={all_passed}")
    if run_error:
        print(f"error={run_error}")
    for item in checklist:
        print(f"check: {item['item']} | pass={item['pass']} | evidence={item['evidence']}")

    return 0 if all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
