from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.constants import TaskStatus, TaskStepCode
from app.models.task import Task
from app.models.task_step import TaskStep

_UNKNOWN_ERROR_CODE = 'UNKNOWN'
_QUALITY_FLAG_NAMES = (
    'overflow',
    'collision',
    'empty_space',
    'alignment_risk',
    'density_imbalance',
    'title_consistency',
)


def _window_start(days: int) -> datetime:
    safe_days = max(1, int(days))
    return datetime.now(timezone.utc) - timedelta(days=safe_days)


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return int(values[0])

    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    lower_value = ordered[lower_index]
    upper_value = ordered[upper_index]
    if lower_index == upper_index:
        return int(lower_value)

    interpolated = lower_value + (upper_value - lower_value) * (position - lower_index)
    return int(round(interpolated))


def _extract_quality_report(output_json: dict | None) -> dict:
    if not isinstance(output_json, dict):
        return {}
    quality_report = output_json.get('quality_report')
    return quality_report if isinstance(quality_report, dict) else output_json


def _extract_quality_flags(output_json: dict | None) -> dict[str, bool]:
    report = _extract_quality_report(output_json)
    signals = report.get('signals') if isinstance(report, dict) else None
    nested_flags = signals.get('flags') if isinstance(signals, dict) else None

    extracted: dict[str, bool] = {}
    if isinstance(nested_flags, dict):
        for flag_name in _QUALITY_FLAG_NAMES:
            if flag_name in nested_flags:
                extracted[flag_name] = bool(nested_flags.get(flag_name))

    for flag_name in _QUALITY_FLAG_NAMES:
        if flag_name in extracted:
            continue
        if flag_name in report:
            extracted[flag_name] = bool(report.get(flag_name))

    return extracted


def get_metrics_overview(db: Session, *, user_id: int, days: int = 7) -> dict:
    window_days = max(1, int(days))
    since = _window_start(window_days).replace(tzinfo=None)

    tasks = list(
        db.scalars(
            select(Task).where(Task.user_id == user_id, Task.created_at >= since)
        ).all()
    )
    task_ids = [task.id for task in tasks]

    total_tasks = len(tasks)
    success_tasks = sum(1 for task in tasks if task.status == TaskStatus.SUCCEEDED)
    failed_tasks = sum(1 for task in tasks if task.status == TaskStatus.FAILED)
    canceled_tasks = sum(1 for task in tasks if task.status == TaskStatus.CANCELED)

    non_canceled_tasks = total_tasks - canceled_tasks
    success_rate = round(success_tasks / non_canceled_tasks, 4) if non_canceled_tasks > 0 else 0.0

    durations_ms: list[int] = []
    for task in tasks:
        if task.status != TaskStatus.SUCCEEDED:
            continue
        if not task.started_at or not task.finished_at:
            continue
        started_at = _as_naive_utc(task.started_at)
        finished_at = _as_naive_utc(task.finished_at)
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        if duration_ms >= 0:
            durations_ms.append(duration_ms)

    p50_duration_ms = _percentile(durations_ms, 0.50)
    p95_duration_ms = _percentile(durations_ms, 0.95)

    self_correct_steps: list[TaskStep] = []
    if task_ids:
        self_correct_steps = list(
            db.scalars(
                select(TaskStep).where(
                    TaskStep.task_id.in_(task_ids),
                    TaskStep.step_code == TaskStepCode.SELF_CORRECT,
                )
            ).all()
        )

    quality_risks: list[float] = []
    quality_flag_counter: Counter[str] = Counter()
    self_correct_task_ids: set[int] = set()
    high_risk_task_ids: set[int] = set()

    for step in self_correct_steps:
        output_json = step.output_json if isinstance(step.output_json, dict) else {}
        quality_report = _extract_quality_report(output_json)
        self_correct_task_ids.add(step.task_id)

        raw_risk = quality_report.get('risk_score')
        if isinstance(raw_risk, (int, float)):
            risk_score = float(raw_risk)
            quality_risks.append(risk_score)
            if risk_score >= 0.75:
                high_risk_task_ids.add(step.task_id)

        for flag_name, flag_value in _extract_quality_flags(output_json).items():
            if flag_value:
                quality_flag_counter[flag_name] += 1

    error_counter: Counter[str] = Counter()
    for task in tasks:
        if task.status != TaskStatus.FAILED:
            continue
        error_code = (task.error_code or '').strip() or _UNKNOWN_ERROR_CODE
        error_counter[error_code] += 1

    error_code_top = [
        {'error_code': code, 'count': count}
        for code, count in error_counter.most_common(5)
    ]

    self_correct_coverage = round(len(self_correct_task_ids) / total_tasks, 4) if total_tasks > 0 else 0.0
    avg_quality_risk = round(sum(quality_risks) / len(quality_risks), 4) if quality_risks else 0.0
    quality_flags_top = [
        {'signal': signal, 'count': count}
        for signal, count in quality_flag_counter.most_common(5)
    ]

    return {
        'total_tasks': total_tasks,
        'success_tasks': success_tasks,
        'failed_tasks': failed_tasks,
        'canceled_tasks': canceled_tasks,
        'success_rate': success_rate,
        'p50_duration_ms': p50_duration_ms,
        'p95_duration_ms': p95_duration_ms,
        'error_code_top': error_code_top,
        'self_correct_coverage': self_correct_coverage,
        'avg_quality_risk': avg_quality_risk,
        'high_risk_tasks': len(high_risk_task_ids),
        'quality_flags_top': quality_flags_top,
    }

