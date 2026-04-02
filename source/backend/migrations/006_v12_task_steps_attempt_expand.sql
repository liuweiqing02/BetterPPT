-- V1.2 phase 1: expand task_steps for attempt-aware history

ALTER TABLE task_steps
  ADD COLUMN attempt_no INT NOT NULL DEFAULT 1 AFTER step_order,
  ADD UNIQUE KEY uk_task_steps_task_order_attempt (task_id, step_order, attempt_no),
  ADD KEY idx_task_steps_task_attempt (task_id, step_code, attempt_no);
