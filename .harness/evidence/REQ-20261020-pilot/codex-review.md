FAIL

- **[P2] `PYTEST_ADDOPTS` leaks beyond arch-check** at [acceptance.ps1:71](/E:/orca/workspaces/story_commandcode/onboarding/scripts/acceptance.ps1:71). It overwrites caller options and remains set for full pytest; subsequent contract runs in the same PowerShell host can inherit it. Set it only on the arch-check child process, or save and restore the original value in `try/finally`, preserving absence.

Otherwise, static inspection confirms contract-only routing, quick/full command order, unchanged JSON schema and exit semantics, and script-based root fallback.

Only this tracked file differs. Untracked task/plan/requirement/evidence files also exist; exclude them from delivery. No files modified or suites run. Model/quota unverified; Hindsight 未写入（只读审查）。

