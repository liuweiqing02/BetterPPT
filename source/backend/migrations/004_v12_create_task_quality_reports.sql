-- V1.2 phase 1: task quality reports

CREATE TABLE IF NOT EXISTS task_quality_reports (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  task_id BIGINT UNSIGNED NOT NULL,
  metric_version VARCHAR(32) NOT NULL DEFAULT 'v1.0',
  evaluated_pages INT UNSIGNED NOT NULL DEFAULT 0,
  pass_flag TINYINT NOT NULL DEFAULT 0,
  layout_offset_ratio DECIMAL(6,4) NULL,
  box_size_deviation_ratio DECIMAL(6,4) NULL,
  style_fidelity_score DECIMAL(6,4) NULL,
  text_slot_match_rate DECIMAL(6,4) NULL,
  image_slot_match_rate DECIMAL(6,4) NULL,
  table_slot_match_rate DECIMAL(6,4) NULL,
  auto_fix_success_rate DECIMAL(6,4) NULL,
  fallback_success_rate DECIMAL(6,4) NULL,
  editable_text_ratio DECIMAL(6,4) NULL,
  locked_page_ratio DECIMAL(6,4) NULL,
  evaluated_scope_json JSON NULL,
  report_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_task_quality_task_metric (task_id, metric_version),
  CONSTRAINT fk_task_quality_task FOREIGN KEY (task_id) REFERENCES tasks(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
