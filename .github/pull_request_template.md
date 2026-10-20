# PR 模板（story_commandcode 多 Agent 流水线）

## 需求 / 任务
- Req / Task：
- 分支：`impl/<REQ-ID>/<taskId>-<employee>`
- 执行器：`codex | dsh | opencode`

## 改动范围（对照任务合同）
- 只改了 `allowedPaths` 内的文件：
- 未改测试 / `pyproject.toml` / `uv.lock` / `AGENTS.md` / `requirements/`
- 共享文件改动（有则列出，无则写「无」）：

## 验收条目（逐条勾选 + 证据路径）
- [ ]
- [ ]

## 测试（主机器会独立复跑）
| 命令 | 退出码 | 日志 |
| --- | --- | --- |
| `powershell -File scripts\acceptance.ps1 -Mode quick` |  | `.harness/acceptance/...` |

## 风险 / 未决
- unknowns：
- 回滚方式：

## 验收者填写
- [ ] verdict: `pass | rework | reject`
- 依据：`.harness/acceptance/acceptance-*.json`
