import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def summarize_output(text: str, max_lines: int = 24, max_chars: int = 4000) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return ""
    if len(cleaned) <= max_chars:
        return cleaned

    lines = cleaned.split("\n")
    if len(lines) <= max_lines:
        return cleaned[:max_chars]

    head_count = max(1, max_lines // 2)
    tail_count = max(1, max_lines - head_count - 1)
    head = lines[:head_count]
    tail = lines[-tail_count:]
    omitted = len(lines) - len(head) - len(tail)
    return "\n".join(head + [f"... ({omitted} lines omitted) ..."] + tail)


@dataclass
class StepResult:
    name: str
    command: list[str]
    cwd: str
    exit_code: int
    duration_seconds: float
    stdout_summary: str
    stderr_summary: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def run_step(name: str, command: list[str], cwd: Path, env: dict[str, str]) -> StepResult:
    started_at = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    duration = time.perf_counter() - started_at
    return StepResult(
        name=name,
        command=command,
        cwd=str(cwd),
        exit_code=proc.returncode,
        duration_seconds=round(duration, 3),
        stdout_summary=summarize_output(proc.stdout),
        stderr_summary=summarize_output(proc.stderr),
    )


def print_step_result(index: int, total: int, result: StepResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    print(f"[{index}/{total}] {result.name}: {status}")
    print(f"  exit_code={result.exit_code} duration={result.duration_seconds:.3f}s")
    if result.stdout_summary:
        print("  stdout_summary:")
        for line in result.stdout_summary.splitlines():
            print(f"    {line}")
    else:
        print("  stdout_summary: <empty>")
    if not result.passed:
        if result.stderr_summary:
            print("  stderr_summary:")
            for line in result.stderr_summary.splitlines():
                print(f"    {line}")
        else:
            print("  stderr_summary: <empty>")


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the full backend regression round.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--front-base", default="http://127.0.0.1:5173")
    parser.add_argument("--source-pdf")
    parser.add_argument("--reference-pptx")
    parser.add_argument("--poll-timeout-seconds", type=int, default=360)
    parser.add_argument("--keep-services", action="store_true")
    parser.add_argument("--keep-api", action="store_true")
    parser.add_argument("--with-browser-check", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
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
    backend_dir = root / "source" / "backend"
    backend_python = backend_dir / ".venv" / "Scripts" / "python.exe"
    if not backend_python.exists():
        backend_python = Path(sys.executable)
    source_pdf = _resolve_source_pdf(root, args.source_pdf)

    report_dir = backend_dir / "tmp_acceptance_runs"
    report_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    steps = [
        (
            "backend_unittest",
            [
                str(backend_python),
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            backend_dir,
        ),
        (
            "page_acceptance_round",
            [
                str(backend_python),
                str(root / "bin" / "page_acceptance_round.py"),
                "--api-base",
                args.api_base,
                "--front-base",
                args.front_base,
                "--source-pdf",
                str(source_pdf),
                "--reference-pptx",
                str(reference_pptx),
                "--poll-timeout-seconds",
                str(args.poll_timeout_seconds),
            ]
            + (["--keep-services"] if args.keep_services else []),
            root,
        ),
        (
            "api_contract_and_actions_regression",
            [
                str(backend_python),
                str(root / "bin" / "api_contract_and_actions_regression.py"),
                "--source-pdf",
                str(source_pdf),
                "--reference-pptx",
                str(reference_pptx),
            ]
            + (["--keep-api"] if args.keep_api else []),
            root,
        ),
    ]

    if args.with_browser_check:
        steps.append(
            (
                "page_browser_acceptance",
                [
                    str(backend_python),
                    str(root / "bin" / "page_browser_acceptance.py"),
                    "--api-base",
                    args.api_base,
                    "--front-base",
                    args.front_base,
                    "--source-pdf",
                    str(source_pdf),
                    "--reference-pptx",
                    str(reference_pptx),
                    "--poll-timeout-seconds",
                    str(args.poll_timeout_seconds),
                ]
                + (["--keep-services"] if args.keep_services else []),
                root,
            )
        )

    results: list[StepResult] = []
    for idx, (name, command, cwd) in enumerate(steps, start=1):
        result = run_step(name, command, cwd, env)
        results.append(result)
        print_step_result(idx, len(steps), result)

    all_passed = all(result.passed for result in results)
    overall_exit_code = 0 if all_passed else next((result.exit_code for result in results if not result.passed), 1)
    report: dict[str, Any] = {
        "run_at": now_iso(),
        "all_passed": all_passed,
        "overall_exit_code": overall_exit_code,
        "steps": [asdict(result) | {"passed": result.passed} for result in results],
    }

    report_path = report_dir / f"full_regression_{timestamp()}.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"report_path={report_path}")
    print(f"all_passed={all_passed}")
    return overall_exit_code


if __name__ == "__main__":
    raise SystemExit(main())
