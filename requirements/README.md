# requirements/ · 需求合同目录（多 Agent 流水线）

每个需求一个子目录：

```
requirements/<REQ-ID>/
  req.md              # 人读：背景/目标/发散说明/风险
  req.json            # 机器读：团队需求合同（见团队仓库 templates/requirements.schema.json）
  tasks/<taskId>.json # 单任务（方向级要求 + allowedPaths + acceptance + tests + techChoices）
  acceptance.json     # 主机器验收结论（verdict/依据/时间）
```

约定：

- `REQ-ID` 格式 `REQ-YYYYMMDD-slug`；一个需求最多 3 个任务（3 人并行）。
- 下发前必须通过 `flow.ps1 validate`（发散 / 技术证据 / allowedPaths 互斥）。
- 执行者只写 `allowedPaths`；测试、依赖、契约、本目录由规划侧维护。
- 证据放 `.harness/evidence/<REQ-ID>/<taskId>/`，不放进本目录。
