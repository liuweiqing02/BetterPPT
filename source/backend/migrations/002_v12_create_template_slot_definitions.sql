-- V1.2 phase 1: template slot definitions

CREATE TABLE IF NOT EXISTS template_slot_definitions (
  id BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  template_profile_id BIGINT UNSIGNED NOT NULL,
  page_no INT NOT NULL,
  slot_key VARCHAR(128) NOT NULL,
  slot_type VARCHAR(32) NOT NULL COMMENT 'text,image,table',
  slot_role VARCHAR(64) NOT NULL COMMENT 'title,subtitle,bullet,figure,datatable,summary',
  bbox_x DECIMAL(8,4) NOT NULL,
  bbox_y DECIMAL(8,4) NOT NULL,
  bbox_w DECIMAL(8,4) NOT NULL,
  bbox_h DECIMAL(8,4) NOT NULL,
  z_index INT NOT NULL DEFAULT 0,
  style_tokens_json JSON NULL,
  constraints_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_tpl_slot (template_profile_id, page_no, slot_key),
  KEY idx_tpl_slot_type (template_profile_id, slot_type),
  CONSTRAINT fk_tpl_slot_profile FOREIGN KEY (template_profile_id) REFERENCES template_profiles(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
