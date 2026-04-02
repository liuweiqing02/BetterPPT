import argparse
import json
from datetime import datetime
from pathlib import Path


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def add_check(checklist, item: str, passed: bool, evidence: str) -> None:
    checklist.append({"item": item, "pass": bool(passed), "evidence": evidence})


def main() -> int:
    parser = argparse.ArgumentParser(description="Check frontend task operation wiring.")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="Repository root. Defaults to the parent directory of bin/.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    frontend_dir = root / "source" / "frontend"
    report_dir = root / "source" / "backend" / "tmp_acceptance_runs"
    report_dir.mkdir(parents=True, exist_ok=True)

    index_path = frontend_dir / "index.html"
    app_path = frontend_dir / "app.js"

    checklist = []
    run_error = None

    try:
        html = index_path.read_text(encoding="utf-8")
        js = app_path.read_text(encoding="utf-8")

        expected_html_ids = [
            "replayPanel",
            "replayRefreshBtn",
            "replayStatus",
            "metricsPanel",
            "metricsRefreshBtn",
            "metricsStatus",
            "metricsDaysInput",
            "taskNoInput",
            "cancelTaskBtn",
            "retryTaskBtn",
            "taskOperationStatus",
        ]
        missing_html_ids = [item for item in expected_html_ids if f'id="{item}"' not in html]
        add_check(
            checklist,
            "index_html_ids",
            not missing_html_ids,
            "missing=" + (",".join(missing_html_ids) if missing_html_ids else "none"),
        )

        expected_js_snippets = [
            "const cancelTaskBtn = document.getElementById('cancelTaskBtn');",
            "const retryTaskBtn = document.getElementById('retryTaskBtn');",
            "cancelTaskBtn?.addEventListener('click', () => executeTaskAction('cancel'));",
            "retryTaskBtn?.addEventListener('click', () => executeTaskAction('retry'));",
            "executeTaskAction('cancel')",
            "executeTaskAction('retry')",
            "const resp = await api(`/tasks/${encodeURIComponent(taskNo)}/${action}`",
            "method: 'POST'",
        ]
        missing_js_snippets = [item for item in expected_js_snippets if item not in js]
        add_check(
            checklist,
            "app_js_cancel_retry_bindings",
            not missing_js_snippets,
            "missing=" + (",".join(missing_js_snippets) if missing_js_snippets else "none"),
        )

        has_guard = "function getTaskNoOrNotify()" in js and "if (!taskNo)" in js and "return null;" in js
        add_check(checklist, "task_no_guard", has_guard, f"guard_present={has_guard}")

    except Exception as exc:
        run_error = str(exc)

    all_passed = bool(checklist) and all(item["pass"] for item in checklist) and not run_error
    report = {
        "run_at": now_iso(),
        "root": str(root),
        "frontend_dir": str(frontend_dir),
        "index_path": str(index_path),
        "app_path": str(app_path),
        "all_passed": all_passed,
        "error": run_error,
        "checklist": checklist,
    }
    report_path = report_dir / f"ui_operation_check_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"report_path={report_path}")
    print(f"all_passed={all_passed}")
    if run_error:
        print(f"error={run_error}")
    for item in checklist:
        print(f"check: {item['item']} | pass={item['pass']} | evidence={item['evidence']}")

    return 0 if all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
