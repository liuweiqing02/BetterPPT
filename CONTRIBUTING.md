# Contributing Guide

感谢你为 BetterPPT 做贡献。

## 开发流程

1. 基于 `main` 创建分支。
2. 分支命名建议：`feat/*`、`fix/*`、`chore/*`、`docs/*`。
3. 提交前本地执行测试。
4. 提交 PR，并按模板填写变更信息与验证证据。

## 本地开发

```powershell
cd source/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 代码质量要求

- 不提交真实密钥、账号信息、私有数据文件。
- 不提交运行时产物（`storage/`, `tmp_*`, `.venv`, `*.db`, `*.log`）。
- 新增功能至少附带最小可复现测试。
- 保持 API 返回结构与错误码语义一致。

## 测试要求

```powershell
source\backend\.venv\Scripts\python.exe -m pytest source\backend\tests\unit -q
.\bin\pre_release_precheck.ps1 -SkipE2E
```

## 提交规范

建议使用 Conventional Commits：

- `feat: ...`
- `fix: ...`
- `docs: ...`
- `chore: ...`
- `refactor: ...`
- `test: ...`

## 安全与合规

如果发现潜在密钥泄露、隐私数据泄露或供应链风险，请不要公开提交 issue，先联系维护者私下处理。
