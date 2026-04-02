import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_check(checklist: list[dict], item: str, passed: bool, evidence: str) -> None:
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


def ensure_task(
    api_base: str,
    source_pdf: Path,
    reference_pptx: Path,
    poll_timeout_seconds: int,
    checklist: list[dict],
) -> str:
    source_file_id = upload_file_via_api(api_base, source_pdf, "pdf_source", "application/pdf")
    reference_file_id = upload_file_via_api(
        api_base,
        reference_pptx,
        "ppt_reference",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )
    add_check(checklist, "upload_source_pdf", source_file_id > 0, f"source_file_id={source_file_id}")
    add_check(checklist, "upload_reference_pptx", reference_file_id > 0, f"reference_file_id={reference_file_id}")

    create = request_json(
        "POST",
        f"{api_base}/tasks",
        {
            "source_file_id": source_file_id,
            "reference_file_id": reference_file_id,
            "detail_level": "balanced",
            "user_prompt": "page browser acceptance",
            "rag_enabled": True,
            "idempotency_key": f"browser-acceptance-{int(time.time() * 1000)}",
        },
    )
    task_no = str(create["data"]["task_no"])
    add_check(checklist, "task_created", bool(task_no), f"task_no={task_no}")

    deadline = time.time() + poll_timeout_seconds
    final_status = ""
    while time.time() < deadline:
        detail = request_json("GET", f"{api_base}/tasks/{task_no}")
        status = str(detail["data"]["status"])
        if status in {"succeeded", "failed", "canceled"}:
            final_status = status
            break
        time.sleep(2)

    add_check(checklist, "task_final_status", final_status == "succeeded", f"status={final_status or 'timeout'}")
    if final_status != "succeeded":
        raise RuntimeError(f"task not succeeded: {final_status or 'timeout'}")

    return task_no


def _wait_selector_count_ge(page, selector: str, count: int, timeout_ms: int) -> int:
    deadline = time.time() + (timeout_ms / 1000.0)
    last = 0
    while time.time() < deadline:
        last = page.locator(selector).count()
        if last >= count:
            return last
        page.wait_for_timeout(250)
    return last


def run_browser_checks(front_base: str, task_no: str, checklist: list[dict]) -> None:
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "playwright is required for browser acceptance. "
            "install with: source/backend/.venv/Scripts/python.exe -m pip install playwright "
            "and then: source/backend/.venv/Scripts/python.exe -m playwright install chromium"
        ) from exc

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"{front_base}/index.html", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector("#taskNoInput", timeout=20000)
            add_check(checklist, "page_loaded", True, "index.html loaded")

            page.click("#refreshTaskListBtn")
            task_count = _wait_selector_count_ge(page, "#taskListPanel .task-item", 1, timeout_ms=12000)
            add_check(checklist, "task_list_rendered", task_count > 0, f"task_item_count={task_count}")
            if task_count > 0:
                page.locator("#taskListPanel .task-item").first.click()

            page.fill("#taskNoInput", task_no)
            page.click("#refreshTaskBtn")
            page.wait_for_timeout(1200)
            task_detail_text = page.locator("#taskDetail").inner_text(timeout=15000)
            add_check(
                checklist,
                "task_detail_refresh",
                task_no in task_detail_text,
                f"task_no_in_detail={task_no in task_detail_text}",
            )

            page.click("#previewBtn")
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('#previewPanel .preview-slide').length > 0 || "
                    "document.querySelector('#previewPanel .preview-empty') !== null",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass
            preview_slide_count = page.locator("#previewPanel .preview-slide").count()
            add_check(checklist, "preview_rendered", preview_slide_count > 0, f"preview_slide_count={preview_slide_count}")

            page.click("#resultBtn")
            try:
                page.wait_for_selector("#downloadLink a", timeout=15000)
            except PlaywrightTimeoutError:
                pass
            link = page.locator("#downloadLink a")
            href = link.first.get_attribute("href") if link.count() > 0 else ""
            add_check(checklist, "result_download_link", bool(href), f"href={href or ''}")

            page.click("#replayRefreshBtn")
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('#replayPanel .replay-item').length > 0 || "
                    "document.querySelector('#replayPanel .task-list-empty') !== null || "
                    "document.querySelector('#replayPanel .preview-empty') !== null",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass
            replay_steps = page.locator("#replayPanel .replay-item").count()
            add_check(checklist, "replay_rendered", replay_steps > 0, f"replay_steps={replay_steps}")

            page.fill("#metricsDaysInput", "7")
            page.click("#metricsRefreshBtn")
            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('#metricsPanel .metric-card').length > 0 || "
                    "document.querySelector('#metricsPanel .metric-error') !== null || "
                    "document.querySelector('#metricsPanel .metric-empty') !== null",
                    timeout=30000,
                )
            except PlaywrightTimeoutError:
                pass
            metric_cards = page.locator("#metricsPanel .metric-card").count()
            add_check(checklist, "metrics_rendered", metric_cards > 0, f"metric_cards={metric_cards}")
        finally:
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run browser-level page acceptance checklist.")
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
    report_dir = backend_dir / "tmp_acceptance_runs"
    log_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)

    checklist: list[dict] = []
    run_error = None
    task_no = None

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
        add_check(checklist, "worker_started", True, f"worker pid={worker_proc.pid}")

        task_no = ensure_task(
            args.api_base,
            source_pdf,
            reference_pptx,
            args.poll_timeout_seconds,
            checklist,
        )

        run_browser_checks(args.front_base, task_no, checklist)
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
        "all_passed": all_passed,
        "error": run_error,
        "checklist": checklist,
    }
    report_path = report_dir / f"page_browser_acceptance_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
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
