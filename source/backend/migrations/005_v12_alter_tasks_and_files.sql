-- V1.2 phase 1: tasks/files incremental fields

ALTER TABLE tasks
  ADD COLUMN template_profile_id BIGINT UNSIGNED NULL AFTER reference_file_id,
  ADD COLUMN quality_score DECIMAL(6,4) NULL AFTER page_count_final,
  ADD COLUMN fallback_used TINYINT NOT NULL DEFAULT 0 AFTER quality_score,
  ADD CONSTRAINT fk_tasks_template_profile FOREIGN KEY (template_profile_id) REFERENCES template_profiles(id);

ALTER TABLE files
  ADD COLUMN scan_status VARCHAR(32) NOT NULL DEFAULT 'pending' AFTER status,
  ADD COLUMN scan_report_json JSON NULL AFTER scan_status,
  ADD COLUMN retention_expire_at DATETIME(3) NULL AFTER scan_report_json,
  ADD KEY idx_files_scan_status (scan_status),
  ADD KEY idx_files_retention_expire_at (retention_expire_at);
