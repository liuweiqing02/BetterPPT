-- V1.2 phase 1: task page mappings and slot fillings

CREATE TABLE IF NOT EXISTS task_page_mappings (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  task_id BIGINT UNSIGNED NOT NULL,
  attempt_no INT NOT NULL DEFAULT 1,
  slide_no INT NOT NULL,
  page_function VARCHAR(64) NOT NULL,
  template_page_no INT NOT NULL,
  mapping_score DECIMAL(5,4) NOT NULL,
  fallback_level TINYINT NOT NULL DEFAULT 0 COMMENT '0=none,1=same_type,2=similar_type,3=generic',
  mapping_json JSON NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_task_page_map_attempt (task_id, attempt_no, slide_no),
  KEY idx_task_page_map_task_attempt (task_id, attempt_no),
  CONSTRAINT fk_task_page_map_task FOREIGN KEY (task_id) REFERENCES tasks(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_slot_fillings (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  task_id BIGINT UNSIGNED NOT NULL,
  attempt_no INT NOT NULL DEFAULT 1,
  slide_no INT NOT NULL,
  slot_key VARCHAR(128) NOT NULL,
  slot_type VARCHAR(32) NOT NULL,
  content_source VARCHAR(32) NOT NULL COMMENT 'llm_text,doc_image,doc_table,fallback_image',
  fill_status VARCHAR(32) NOT NULL COMMENT 'success,adjusted,fallback,failed',
  quality_score DECIMAL(5,4) NULL,
  overflow_flag TINYINT NOT NULL DEFAULT 0,
  overlap_flag TINYINT NOT NULL DEFAULT 0,
  fill_json JSON NOT NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_task_slot_fill_attempt (task_id, attempt_no, slide_no, slot_key),
  KEY idx_task_slot_task_slide (task_id, attempt_no, slide_no),
  KEY idx_task_slot_status (task_id, fill_status),
  CONSTRAINT fk_task_slot_task FOREIGN KEY (task_id) REFERENCES tasks(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
