-- BetterPPT initial schema (M1)

CREATE TABLE IF NOT EXISTS users (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(64) NOT NULL,
  email VARCHAR(128) NULL,
  password_hash VARCHAR(255) NULL,
  status TINYINT NOT NULL DEFAULT 1,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_users_username (username),
  UNIQUE KEY uk_users_email (email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS files (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  file_role VARCHAR(32) NOT NULL,
  storage_provider VARCHAR(32) NOT NULL DEFAULT 'local',
  storage_path VARCHAR(512) NOT NULL,
  filename VARCHAR(255) NOT NULL,
  ext VARCHAR(16) NOT NULL,
  mime_type VARCHAR(128) NULL,
  file_size BIGINT UNSIGNED NOT NULL,
  checksum_sha256 CHAR(64) NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'uploaded',
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  KEY idx_files_user_role (user_id, file_role),
  CONSTRAINT fk_files_user FOREIGN KEY (user_id) REFERENCES users(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tasks (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  task_no VARCHAR(64) NOT NULL,
  source_file_id BIGINT UNSIGNED NOT NULL,
  reference_file_id BIGINT UNSIGNED NOT NULL,
  result_file_id BIGINT UNSIGNED NULL,
  detail_level VARCHAR(16) NOT NULL,
  user_prompt TEXT NULL,
  rag_enabled TINYINT NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL,
  current_step VARCHAR(64) NULL,
  progress INT NOT NULL DEFAULT 0,
  page_count_estimated INT NULL,
  page_count_final INT NULL,
  error_code VARCHAR(64) NULL,
  error_message VARCHAR(512) NULL,
  retry_count INT NOT NULL DEFAULT 0,
  idempotency_key VARCHAR(128) NULL,
  started_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_tasks_task_no (task_no),
  UNIQUE KEY uk_tasks_idempotency (user_id, idempotency_key),
  KEY idx_tasks_user_created (user_id, created_at),
  CONSTRAINT fk_tasks_user FOREIGN KEY (user_id) REFERENCES users(id),
  CONSTRAINT fk_tasks_source_file FOREIGN KEY (source_file_id) REFERENCES files(id),
  CONSTRAINT fk_tasks_reference_file FOREIGN KEY (reference_file_id) REFERENCES files(id),
  CONSTRAINT fk_tasks_result_file FOREIGN KEY (result_file_id) REFERENCES files(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_steps (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  task_id BIGINT UNSIGNED NOT NULL,
  step_code VARCHAR(64) NOT NULL,
  step_order INT NOT NULL,
  step_status VARCHAR(32) NOT NULL,
  input_json JSON NULL,
  output_json JSON NULL,
  started_at DATETIME(3) NULL,
  finished_at DATETIME(3) NULL,
  duration_ms INT NULL,
  error_code VARCHAR(64) NULL,
  error_message VARCHAR(512) NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_task_steps_task_order (task_id, step_order),
  CONSTRAINT fk_task_steps_task FOREIGN KEY (task_id) REFERENCES tasks(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_events (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  task_id BIGINT UNSIGNED NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  event_time DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  message VARCHAR(512) NULL,
  payload_json JSON NULL,
  KEY idx_task_events_task_time (task_id, event_time),
  CONSTRAINT fk_task_events_task FOREIGN KEY (task_id) REFERENCES tasks(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS template_profiles (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  file_id BIGINT UNSIGNED NOT NULL,
  profile_version VARCHAR(32) NOT NULL DEFAULT 'v1',
  total_pages INT NOT NULL,
  cluster_count INT NOT NULL,
  embedding_model VARCHAR(64) NOT NULL DEFAULT 'vit-base',
  llm_model VARCHAR(64) NOT NULL DEFAULT 'gpt-4.1-mini',
  summary_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_template_profiles_file_ver (file_id, profile_version),
  CONSTRAINT fk_template_profiles_file FOREIGN KEY (file_id) REFERENCES files(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS template_page_schemas (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  template_profile_id BIGINT UNSIGNED NOT NULL,
  page_no INT NOT NULL,
  cluster_label VARCHAR(64) NOT NULL,
  page_function VARCHAR(64) NOT NULL,
  layout_schema_json JSON NOT NULL,
  style_tokens_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_tpl_schema_page (template_profile_id, page_no),
  CONSTRAINT fk_tpl_page_schema_profile FOREIGN KEY (template_profile_id) REFERENCES template_profiles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
