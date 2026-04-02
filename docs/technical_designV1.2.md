# BetterPPT 技术设计文档（TDD）V1.2

## 1. 文档目标与范围

本文档用于指导 BetterPPT 的前后端、算法、测试协作开发，覆盖：

- 系统整体技术架构
- 数据库设计（MySQL + Redis）
- 接口设计（REST API）
- 任务状态机与流转规则
- 回退与质量保障机制

本文在 `TDD V1.1` 基础上做增量升级，保留 V1.1 已落地链路，不删除历史定义。

## 1.1 V1.2 相比 V1.1 技术变更清单

### 新增

- 新增模板资产化流水线：页面分类、槽位 schema、样式 token、母版语义抽取与入库。
- 新增元素级编辑执行器：以模板页复制为起点，按 `edit_ops` 对对象级增删改。
- 新增内容槽位映射层：支持文本/图像/表格三类填充。
- 新增质量修正与回退链路：溢出、遮挡、错位检测，失败后分级回退。
- 新增质量报告产物：记录布局偏差、样式保真度、槽位匹配率、可编辑性。

### 修改

- 修改 Worker 步骤编排：由“规划后直接导出”升级为“资产化 -> 槽位映射 -> 元素编辑 -> 修正 -> 导出”。
- 修改模板分析产物模型：从聚类摘要升级为可执行模板资产模型。
- 修改任务步骤日志：要求记录映射决策、修正动作、回退原因。

### 删除

- 删除“空白页重建”为默认主路径，仅保留为故障兜底路径。
- 删除“模板仅风格参考”的处理逻辑。

## 2. 技术架构设计

## 2.1 架构分层

- `source/frontend`：上传、参数配置、任务进度、结果预览下载、质量报告查看。
- `source/backend`：鉴权、任务编排、资产管理、接口聚合。
- `Agent Worker`：异步执行（解析、检索、规划、资产化、映射、编辑、修正、导出）。
- `MySQL`：持久化任务、模板资产、映射与质量数据。
- `Redis`：任务队列、进度缓存、分布式锁、热点资产缓存。
- `Object Storage`：源文件、中间产物、结果 PPT。

## 2.2 逻辑流程

1. 前端上传 PDF 与参考 PPT，创建任务。
2. 后端校验并写入 `tasks`，推入 Redis Stream。
3. Worker 解析文档、分析模板并构建模板资产。
4. Worker 生成页级大纲，将内容映射到模板槽位。
5. Worker 执行元素级编辑（文本/图像/表格）。
6. Worker 执行质量检测与自动修正。
7. 修正失败触发回退策略；成功后导出可编辑 PPT。
8. 前端查询任务状态、质量报告并下载结果。

## 2.3 Agent Worker 技术要求（MVP）

### 2.3.1 执行原则

- 所有核心 Agent 通过统一 LLM API 客户端调用（OpenAI 兼容协议）。
- 统一环境变量：
  - `LLM_API_BASE=https://api.openai.com/v1`
  - `LLM_API_KEY=<secret>`
  - `LLM_MODEL=gpt-4.1-mini`
- 本地模型仅用于辅助能力（ViT embedding、OCR、表格结构检测）。

### 2.3.2 Agent 拆分与 I/O 契约

1. `DocumentParseAgent`（step=`parse_pdf`）
   - 输入：PDF 文本块、图像块、表格块
   - 输出：`sections[]`、`images[]`、`tables[]`、`key_facts[]`
2. `TemplateAssetizeAgent`（step=`assetize_template`）
   - 输入：参考 PPT 页面对象、母版信息
   - 输出：`asset_pages[]`、`slots[]`、`style_tokens`、`layout_semantics`
3. `RagRetrieveAgent`（step=`rag_retrieve`，可选）
   - 输入：`user_prompt`/自动 query
   - 输出：`retrieved_chunks[]`、`citations[]`
4. `OutlinePlanAgent`（step=`plan_slides`）
   - 输入：文档结构、挡位、检索结果
   - 输出：`slide_plan[]`
5. `SlotMappingAgent`（step=`map_slots`）
   - 输入：`slide_plan[]`、模板槽位资产
   - 输出：`page_mappings[]`、`slot_fill_plan[]`
6. `SlideGenerateAgent`（step=`generate_slides`）
   - 输入：`slot_fill_plan[]`、文档素材
   - 输出：`edit_ops[]`
7. `SelfCorrectAgent`（step=`self_correct`）
   - 输入：布局检测信号、样式偏差
   - 输出：`fix_ops[]`、`fallback_decision`、`quality_report`

### 2.3.3 Worker 落库约束

- 每步必须记录 `task_steps.input_json/output_json/duration_ms/error_code`。
- 映射决策、回退动作必须写 `task_events`。
- 质量报告必须落库并可通过接口查询。

## 3. 数据库设计

## 3.1 MySQL 设计原则

- 字符集：`utf8mb4`
- 时间字段：统一 `datetime(3)`，UTC 存储
- 主键：`bigint unsigned` 自增
- 幂等：关键写操作使用 `idempotency_key`
- 归属校验：任务、文件、模板资产均校验 `user_id`

## 3.2 表结构清单

沿用 V1.1：

- `users`
- `files`
- `tasks`
- `task_steps`
- `task_events`
- `template_profiles`
- `template_page_schemas`

V1.2 新增：

- `template_slot_definitions`：模板槽位定义
- `task_page_mappings`：任务级页面映射结果
- `task_slot_fillings`：任务级槽位填充结果
- `task_quality_reports`：任务质量报告

## 3.3 核心表结构增量（V1.2 DDL）

```sql
CREATE TABLE template_slot_definitions (
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

CREATE TABLE task_page_mappings (
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

CREATE TABLE task_slot_fillings (
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

CREATE TABLE task_quality_reports (
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
  evaluated_scope_json JSON NULL COMMENT '评估口径与样本范围，含排除页面类型',
  report_json JSON NULL,
  created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
  updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  UNIQUE KEY uk_task_quality_task_metric (task_id, metric_version),
  CONSTRAINT fk_task_quality_task FOREIGN KEY (task_id) REFERENCES tasks(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

## 3.4 任务表增量字段（V1.2）

```sql
ALTER TABLE tasks
  ADD COLUMN template_profile_id BIGINT UNSIGNED NULL AFTER reference_file_id,
  ADD COLUMN quality_score DECIMAL(6,4) NULL AFTER page_count_final,
  ADD COLUMN fallback_used TINYINT NOT NULL DEFAULT 0 AFTER quality_score,
  ADD CONSTRAINT fk_tasks_template_profile FOREIGN KEY (template_profile_id) REFERENCES template_profiles(id);
```

## 3.5 安全与留存字段增量（V1.2）

```sql
ALTER TABLE files
  ADD COLUMN scan_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending,scanning,clean,suspicious,blocked',
  ADD COLUMN scan_report_json JSON NULL,
  ADD COLUMN retention_expire_at DATETIME(3) NULL,
  ADD KEY idx_files_scan_status (scan_status),
  ADD KEY idx_files_retention_expire_at (retention_expire_at);

ALTER TABLE task_steps
  ADD COLUMN attempt_no INT NOT NULL DEFAULT 1 AFTER step_order,
  DROP INDEX uk_task_steps_task_order,
  ADD UNIQUE KEY uk_task_steps_task_order_attempt (task_id, step_order, attempt_no),
  ADD KEY idx_task_steps_task_attempt (task_id, step_code, attempt_no);
```

## 3.6 Redis Key 设计（V1.2）

- 保留 V1.1：
  - `stream:tasks:pending`
  - `group:tasks:workers`
  - `lock:task:{task_no}`
  - `task:progress:{task_no}`
  - `task:events:{task_no}`
- 新增：
  - `cache:template:profile:{file_id}`（Hash, EX=7d）：模板资产缓存
  - `task:quality:{task_no}`（Hash, EX=7d）：质量指标缓存
  - `task:fallback:{task_no}`（List, EX=7d）：回退动作记录

## 4. 接口设计

## 4.1 API 约定

- Base URL：`/api/v1`
- 鉴权：`Authorization: Bearer <token>`
- 返回格式：

```json
{
  "code": 0,
  "message": "ok",
  "data": {}
}
```

- `code != 0` 表示业务错误。
- HTTP 状态码映射：
  - `200`：请求成功
  - `400`：参数错误/业务校验失败
  - `401`：未鉴权或 token 失效
  - `403`：资源无权限访问
  - `404`：任务/文件不存在
  - `409`：状态冲突、幂等冲突
  - `429`：限流触发
  - `500`：系统内部错误

## 4.2 接口列表

保留 V1.1：

1. `POST /files/upload-url`
2. `POST /files/complete`
3. `POST /tasks`
4. `GET /tasks/{task_no}`
5. `GET /tasks/{task_no}/events`
6. `POST /tasks/{task_no}/retry`
7. `POST /tasks/{task_no}/cancel`
8. `GET /tasks/{task_no}/result`
9. `GET /tasks/{task_no}/preview`
10. `GET /tasks`

V1.2 新增：

11. `GET /tasks/{task_no}/quality-report`
12. `GET /tasks/{task_no}/mappings`
13. `POST /templates/{file_id}/assetize`（可选预热）
14. `GET /templates/{file_id}/assets`

## 4.3 关键接口增量定义（V1.2）

### 4.3.1 查询预览（补齐 V1.1 契约）

- `GET /api/v1/tasks/{task_no}/preview`
- 调用条件：仅 `tasks.status=succeeded` 可调用。
- 权限规则：仅任务归属用户可调用。

响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "task_no": "T202604020001",
    "slides": [
      {
        "slide_no": 1,
        "image_url": "https://example.com/previews/T202604020001/1.png"
      },
      {
        "slide_no": 2,
        "image_url": "https://example.com/previews/T202604020001/2.png"
      }
    ],
    "expires_in": 3600
  }
}
```

### 4.3.2 查询质量报告

- `GET /api/v1/tasks/{task_no}/quality-report`

响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "task_no": "T202604020001",
    "metric_version": "v1.0",
    "evaluated_pages": 16,
    "pass_flag": 1,
    "layout_offset_ratio": 0.021,
    "box_size_deviation_ratio": 0.064,
    "style_fidelity_score": 0.93,
    "text_slot_match_rate": 0.97,
    "image_slot_match_rate": 0.92,
    "table_slot_match_rate": 0.88,
    "auto_fix_success_rate": 0.96,
    "fallback_success_rate": 1.0,
    "editable_text_ratio": 0.98,
    "locked_page_ratio": 0.00
  }
}
```

### 4.3.3 查询页面与槽位映射

- `GET /api/v1/tasks/{task_no}/mappings?attempt_no=latest&cursor=<opaque>&limit=100`
- 参数约束：
  - `attempt_no` 默认 `latest`，返回最近一次尝试结果。
  - `limit` 默认 `100`，最大 `500`。
  - `cursor` 为空时返回首页。

响应示例：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "task_no": "T202604020001",
    "attempt_no": 2,
    "items": [
      {
        "slide_no": 3,
        "template_page_no": 12,
        "fallback_level": 1,
        "slot_fillings": [
          { "slot_key": "title", "fill_status": "success" },
          { "slot_key": "table_main", "fill_status": "fallback" }
        ]
      }
    ],
    "next_cursor": "eyJpZCI6MTAyNH0="
  }
}
```

### 4.3.4 模板资产预热

- `POST /api/v1/templates/{file_id}/assetize`

用途：提前构建模板资产，降低任务首次生成延时。

### 4.3.5 质量指标计算口径（V1.2.2）

- 指标版本：`metric_version`，默认 `v1.0`，后续口径调整必须升级版本号。
- 统计粒度：
  - 布局与样式类：对象级（shape/textbox）
  - 槽位匹配类：槽位级（text/image/table slot）
  - 回退与修正类：任务级（按事件统计）
- 样本页范围：
  - 默认评估内容页与章节页。
  - 默认排除封面与目录页，写入 `evaluated_scope_json.excluded_page_types=["cover","toc"]`。
- 分母定义：
  - `layout_offset_ratio`：发生偏移的对象数 / 评估对象总数
  - `box_size_deviation_ratio`：尺寸偏差超阈值对象数 / 评估对象总数
  - `text_slot_match_rate`：匹配成功文本槽位数 / 文本槽位总数
  - `image_slot_match_rate`：匹配成功图像槽位数 / 图像槽位总数
  - `table_slot_match_rate`：匹配成功表格槽位数 / 表格槽位总数
  - `auto_fix_success_rate`：自动修正成功次数 / 自动修正触发次数
  - `fallback_success_rate`：回退后成功次数 / 回退触发次数
  - `editable_text_ratio`：可编辑文本对象数 / 文本对象总数
  - `locked_page_ratio`：锁死或整页位图化页面数 / 总页面数
- 判定规则：`pass_flag=1` 需满足 PRD 第 13 章全部阈值。
- 可复算要求：测试侧仅依赖 `task_slot_fillings`、`task_events`、导出 PPT 解析结果应可复算一致。

## 4.4 错误码建议（增量）

| 错误码 | HTTP Code | retryable | 前端处理建议 | 用户文案建议 |
|---|---|---|---|---|
| `1001` 参数非法 | `400` | 否 | 阻断提交并高亮字段 | 请求参数有误，请检查后重试 |
| `1002` 文件不存在或无权限 | `404/403` | 否 | 返回上传页并提示重传 | 文件不存在或无访问权限 |
| `1003` 文件类型不支持 | `400` | 否 | 阻断提交，提示格式要求 | 文件格式不支持，请上传 PDF/PPTX |
| `1004` 任务状态不允许该操作 | `409` | 否 | 刷新任务状态后禁用按钮 | 当前任务状态不支持该操作 |
| `1005` 幂等冲突 | `409` | 是（短时） | 拉取冲突任务详情并复用 | 请求重复，已返回已存在任务 |
| `2101` 模板资产化失败 | `500` | 是（自动 1 次 + 手动） | 引导重试；若仍失败建议更换模板 | 模板解析失败，请重试或更换模板 |
| `2102` 槽位映射失败 | `500` | 是（自动 1 次 + 手动） | 展示回退结果；失败则提示调整输入 | 内容与模板匹配失败，已尝试回退 |
| `2103` 表格还原失败（已降级） | `200` | 否 | 以 warning 展示，不中断下载 | 表格已按兼容模式展示 |
| `2104` 布局修正失败（触发回退） | `500` | 是（自动） | 继续监听回退阶段事件 | 版面自动修正失败，正在回退处理 |
| `2105` 回退后仍不可交付 | `500` | 是（手动） | 提供重试与反馈入口 | 当前模板无法稳定生成，请重试或更换模板 |
| `3001` 系统繁忙 | `429` | 是（指数退避） | 客户端退避重试 | 系统繁忙，请稍后再试 |
| `9000` 系统内部错误 | `500` | 是（手动） | 展示工单号并引导反馈 | 系统异常，请稍后重试 |

回退链路错误绑定：

- `2104`：`task_events.payload_json` 必须记录 `from_step/to_step/fallback_level/reason_code/attempt_no`。
- `2105`：记录最终失败回退路径与失败原因，供全链路回放。

## 5. 任务状态机

## 5.1 状态定义

任务状态保持不变：

- `created`
- `validating`
- `queued`
- `running`
- `succeeded`
- `failed`
- `canceled`

`tasks.current_step` 枚举说明：

- `validate_input`（仅在 `status=validating`）
- `parse_pdf`、`analyze_template`、`assetize_template`、`rag_retrieve`、`plan_slides`、`map_slots`、`generate_slides`、`self_correct`、`export_ppt`（仅在 `status=running`）

`running` 内部步骤为：

1. `parse_pdf`
2. `analyze_template`
3. `assetize_template`
4. `rag_retrieve`（可选）
5. `plan_slides`
6. `map_slots`
7. `generate_slides`
8. `self_correct`
9. `export_ppt`

## 5.2 状态转移规则

- `created -> validating -> queued -> running -> succeeded`
- `validating` 阶段校验失败：`validating -> failed`
- 任一关键步骤不可恢复失败：`running -> failed`
- 用户取消：`queued/running -> canceled`
- 失败重试：`failed -> queued`

回退规则：

- `self_correct` 失败后允许一次内部回退到 `map_slots/generate_slides` 重试。
- 回退重试仍失败则标记 `failed`。
- 每次回退必须写标准事件载荷：
  - `from_step`
  - `to_step`
  - `fallback_level`
  - `reason_code`
  - `attempt_no`
- 事件载荷示例：

```json
{
  "from_step": "self_correct",
  "to_step": "map_slots",
  "fallback_level": 2,
  "reason_code": "OVERFLOW_UNRESOLVED",
  "attempt_no": 2
}
```

## 5.3 进度映射建议（V1.2）

- `validating.validate_input`：0-5
- `running.parse_pdf`：6-15
- `running.analyze_template`：16-25
- `running.assetize_template`：26-40
- `running.rag_retrieve`：41-48
- `running.plan_slides`：49-60
- `running.map_slots`：61-72
- `running.generate_slides`：73-88
- `running.self_correct`：89-96
- `running.export_ppt`：97-100

## 6. Worker 执行与幂等设计

- 消费前获取 `lock:task:{task_no}`，避免重复执行。
- 步骤级幂等：
  - 已成功 step 不重复执行。
  - `map_slots/generate_slides/self_correct` 允许带版本号重入（`attempt_no`）。
- `attempt_no` 落库约束：
  - `task_steps`：同 `task_id + step_order + attempt_no` 唯一。
  - `task_page_mappings`：同 `task_id + attempt_no + slide_no` 唯一。
  - `task_slot_fillings`：同 `task_id + attempt_no + slide_no + slot_key` 唯一。
  - 所有尝试历史保留，不覆盖前次结果。
- 回退可观测性约束：
  - 触发 `2104/2105` 时，`task_events.payload_json` 必须含 `from_step/to_step/fallback_level/reason_code/attempt_no`。
- 导出成功原子更新：
  - `tasks.status = succeeded`
  - `tasks.result_file_id`、`tasks.quality_score`
  - `task_quality_reports` 写入完成

## 7. 安全与合规

- 文件白名单校验（扩展名 + MIME）。
- 恶意文件扫描：上传后进入 `scan_status=pending/scanning`，仅 `clean` 文件可进入任务执行。
- 扫描结果落库：`files.scan_status`、`files.scan_report_json`。
- 下载链接短时效签名。
- 审计字段记录：操作人、时间、IP、`task_no`。
- 模板资产读取必须做用户归属校验，禁止跨租户复用。
- 留存策略与到期清理：
  - 源文件保留 7 天，结果文件保留 30 天，审计日志保留 180 天。
  - 每日定时任务按 `retention_expire_at` 执行到期清理并记录审计事件。
- 用户删除流程：
  1. 用户提交删除请求（任务/文件维度）。
  2. 后端校验归属并执行软删除。
  3. 异步清理对象存储与缓存。
  4. 记录删除审计日志与完成时间。

## 8. 开发落地建议

## 8.1 目录建议

- `source/backend/app/api`
- `source/backend/app/services`
- `source/backend/app/workers`
- `source/backend/app/models`
- `source/backend/migrations`
- `source/backend/app/quality`
- `source/frontend/src/pages`
- `source/frontend/src/services`

## 8.2 MVP 实施顺序（V1.2）

1. 增加模板资产化表与迁移脚本。
2. 实现 `assetize_template` 与 `map_slots` 步骤。
3. 升级 `generate_slides` 为元素级编辑执行器。
4. 接入 `self_correct` 与回退链路。
5. 实现质量报告落库与查询接口。
6. 完成回归测试（布局偏差、槽位匹配、可编辑性）。

---

如与 V1.1 实现冲突，以“保留可运行链路 + 增量切换”为原则，逐步将空白页路径下线为仅故障兜底。
