-- V1.2 phase 1: drop legacy task_steps unique key after app switch

ALTER TABLE task_steps
  DROP INDEX uk_task_steps_task_order;
