PASS — no remaining findings.

- `PYTEST_ADDOPTS` override surrounds only arch-check; `finally` restores the prior value or removes it when originally absent.
- Contract mode runs only `tests/contract`, skipping doctor and arch-check.
- Quick/full sequencing, reporting, and exit behavior remain preserved.
- Only `scripts/acceptance.ps1` has tracked changes; untracked task/plan/requirement/evidence files remain outside delivery.

Static review only; no files modified or suites run. Hindsight 未写入（只读审查）。

