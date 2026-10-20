1. **Preflight:** Confirm the required `gpt-6-sol` planning model, `gpt-6-luna` execution model, and live quota before implementation; these were not verifiable here. Treat this as one small implementation-and-validation stage.

2. **Edit only `scripts/acceptance.ps1`:**

   - **Lines 4–10:** Document `contract` as running only contract tests; add its `.EXAMPLE` command.
   - **Line 14:** Change validation to `[ValidateSet('quick','full','contract')]`; retain `quick` as the default.
   - **Lines 66–71:** Guard doctor and arch-check with `$Mode -ne 'contract'`. Add a contract branch invoking pytest through `Invoke-Check`; preserve existing quick/full behavior.
   - Reuse `$bt = Join-Path $cacheRoot ("pytest\story-{0}" -f $stamp)` and the existing pytest timeout. The resulting command must be:

     ```powershell
     uv run --extra dev pytest tests/contract -p no:cacheprovider "--basetemp=$bt"
     ```

   - **Lines 73–89:** Reuse the existing results, JSON schema, and exit handling: success exits `0`; any failed check exits `1`.

3. **Preserve constraints:** Save the script as **UTF-8 with BOM** for PowerShell 5.1. Keep `$ArgList`; avoid automatic-variable names such as `$Args`. Use cached dependencies with uv offline mode; add no dependencies or paid/cloud calls.

4. **Run these two verification commands after implementation**, recording each actual exit code:

   ```powershell
   powershell -File scripts\acceptance.ps1 -Mode contract
   powershell -File scripts\acceptance.ps1 -Mode quick
   ```

   Both must exit `0`. Inspect their generated reports: contract must contain `"mode": "contract"` and only the contract pytest result; quick must retain doctor and arch-check.

5. **Check delivery scope:** `git diff --name-only` must list only `scripts/acceptance.ps1`. Report changed sections, both exit codes, and the diff file list. No files or acceptance runs are part of this planning task.

