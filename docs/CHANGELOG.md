# CHANGELOG

## [v0.1.2] - 2026-04-03

### Added
- 新增前端 MVP 四页面流：任务创建、任务列表、任务详情（进度+日志）、结果预览与下载。
- 新增旧版单页联调入口：`/legacy.html`，用于平滑迁移。
- 新增上传约束接口：`GET /api/v1/files/upload-constraints`。
- 新增并发活跃窗口配置：`TASK_CONCURRENCY_ACTIVE_WINDOW_MINUTES`，避免历史僵尸任务长期占用并发额度。

### Changed
- 任务详情契约扩展：`fallback_state/fallback_attempt_no`。
- 回退事件标准化：`fallback_started/fallback_finished/fallback_failed`。
- README 与 DEPLOYMENT 同步更新到 `v0.1.2` 交付口径与部署说明。

## [V1.2.4] - 2026-04-03

### Fixed
- 修复 PRD 页面定义与验收口径不一致：明确为“4 个独立页面 + 失败重试入口”，不再将失败重试定义为独立页面。
- 修复前端文件大小前置拦截契约缺失：TDD 补充 `GET /api/v1/files/upload-constraints` 接口及返回结构。
- 修复“回退中”状态不可稳定判定：新增任务详情字段 `fallback_state/fallback_attempt_no` 及后端事件映射规则。

### Changed
- 前端页面路由统一为 `/app/*`，后端 API 维持 `/api/v1/*`，补充网关按前缀分流要求，规避同名路由冲突。
- PRD/TDD 补充界面性能统计口径：统计窗口、样本量下限、冷/热启动拆分、弱网排除规则。
## [V1.2.3] - 2026-04-03

### Added
- TDD 新增“用户端界面与交互技术设计”章节，覆盖 MVP 页面范围：任务创建、任务列表、任务详情（进度+日志）、结果预览与下载、失败重试。
- 新增前端交互实现要求：表单校验、上传进度、轮询与状态提示、失败可恢复、空状态与错误文案。
- 新增桌面端体验与性能指标：分辨率适配目标、首屏与关键操作响应阈值、前端埋点与告警策略。

### Changed
- `technical_designV1.2.md` 章节顺延：原“开发落地建议”由第 8 章调整为第 9 章。
## [V1.2.2] - 2026-04-02

### Added
- TDD 补齐预览接口契约：`GET /api/v1/tasks/{task_no}/preview`，明确 `slides`、`expires_in`、仅 `succeeded` 可调用。
- TDD 新增映射查询分页与体量控制：`cursor/limit`，并约定默认返回最近一次 `attempt_no`。
- TDD 新增质量指标计算口径章节：分母定义、统计粒度、样本页范围、排除页类型与复算要求。
- TDD 新增回退事件标准载荷：`from_step/to_step/fallback_level/reason_code/attempt_no`。

### Changed
- 状态机语义统一：`validate_input` 归属 `validating`，不再列入 `running` 步骤；状态定义、步骤列表、进度映射三处已对齐。
- 错误码定义升级为表格化契约：补齐 HTTP 状态码映射与 `retryable` 语义，并给出前端处理建议与用户文案。
- 安全治理补齐到 TDD：恶意文件扫描、留存策略（7/30/180）、到期清理任务、用户删除流程，并新增 `files.scan_status/scan_report_json/retention_expire_at` 字段。
- `attempt_no` 闭环落库：`task_steps`、`task_page_mappings`、`task_slot_fillings` 增加 `attempt_no` 与唯一键调整，支持多次尝试历史保留。
- 质量报告字段扩展：新增 `metric_version`、`evaluated_pages`、`pass_flag`，并通过质量报告接口返回。
- PRD 版本规划对齐现状：M2 改为“已完成/沿用”，避免与现有能力冲突。
- PRD 性能目标变更说明补齐：P50/P95 由 `5/10` 调整为 `6/12`，原因是链路新增资产化、槽位映射与质量门控；并补充容量假设。

## [V1.2.1] - 2026-04-02

### Added
- 新增技术文档 V1.2：`docs/technical_designV1.2.md`。
- 新增 V1.2 技术增量内容：模板资产化流水线、槽位映射层、元素级编辑执行器、质量报告与回退链路。

### Changed
- 重构 `docs/requirementsV1.2.md`：改为在 V1.1 基础上的增量修订版本，保留 V1.1 既有框架与关键约束。
- 明确文档保留策略：历史版本不覆盖，采用并行版本文件维护（V1.1 与 V1.2 同时保留）。
- 更新本 CHANGELOG 维护说明：同时记录 PRD 与 TDD 的版本演进。

### Fixed
- 修复此前 V1.2 文档相对 V1.1 信息继承不完整的问题（章节与技术约束已补齐）。

## [V1.2] - 2026-04-02

### Added
- 新增“模板资产化与元素保留策略”章节。
- 新增“内容槽位映射与填充规则（文本/图像/表格）”章节。
- 新增 V1.2 相比 V1.1 的变更清单（新增/修改/删除）。
- 新增范围边界定义：MVP 必做、后续版本可做、明确不做。
- 新增风险与回退策略（模板解析失败、槽位不匹配、图表/表格还原失败、版面溢出等）。
- 新增用户价值与业务价值章节，用于产品/研发/测试优先级对齐。
- 新增量化验收指标：结构语义保留、布局偏差、样式保真、槽位匹配率、自动修正与回退成功率、可编辑性。

### Changed
- 生成主路径从“空白页新建 + 文本写入”升级为“模板页复制 + 元素级增删改”。
- 参考 PPT 从“风格参考”升级为“可编辑模板资产”。
- 模板分析输出从“页面聚类结果”升级为“页面分类 + 槽位 schema + 样式 token + 母版语义”。
- 质量评估口径从“主观相似”升级为“可量化指标 + 人工抽检”。
- 失败处理从“任务级失败”升级为“步骤级修正 + 回退 + 可审计日志”。

### Removed
- 删除“默认新建空白页作为主要导出方案”的产品口径。
- 删除“模板仅用于风格提示、不参与结构约束”的策略。
- 删除“图像/表格仅文本摘要即可通过”的验收口径。

## [V1.1] - 2026-04-02

### Added
- 建立 BetterPPT 初版 PRD（参考模板驱动的 PDF -> PPT 自动生成）。
- 明确上传、任务流转、基础模板分析、LLM 大纲规划、PPT 导出主链路。
- 定义长度挡位（精炼/适中/详细）与 RAG 可选增强策略。
- 定义任务状态流转、非功能指标与基础风险应对。

---

维护说明：
- 本文件记录文档级版本演进（PRD + TDD）。
- 历史版本采用并行文件保留，不覆盖旧版。
- 当前对应文档：
  - PRD：`docs/requirements.md`（V1.1）、`docs/requirementsV1.2.md`（V1.2）
  - TDD：`docs/technical_design.md`（V1.1）、`docs/technical_designV1.2.md`（V1.2）


