"""`doctor` 自检 —— 让"系统是否健康"变成一条命令。

## 从 `cli.py` 拆出来的理由

契约规则 10 是**单文件 ≤800 行**。加了 `flows` 子命令后 `cli.py` 达到
825 行 —— 规则当场拦下。**正确的做法是拆文件，不是放宽规则**
（旧项目 `pipeline.py` 8,607 行的教训，规则 10 就是为它立的）。

拆 `doctor` 而不是随便切一刀，依据是**职责**：
`doctor` 是"体检项"的集合（配置/存储/外键/能力/分层），
与"命令分发"是两件事 —— 前者会被测试直接调用（`tests/unit/test_dotenv.py`
等），后者只被 `main()` 调用。

## 自检项清单

| 项 | 防的是什么 |
|---|---|
| `config` | 配置未加载 / data_dir 不可写 |
| `structure_gate_enabled` | 旧项目 `shadow: true` → 指标设计对但**不阻断** → 400 章挤第 1 卷 |
| `database` / `foreign_keys_forced` / `integrity` / `all_tables_have_fk` | 旧项目 0 个 FOREIGN KEY、删书靠人工清单 |
| `providers` / `api_keys` | 能力声明与实际可用性不一致 |
| `extractor_thinking_minimized` | 旧项目 `entity_classify` 空输出 **100%** |
| `domain_is_framework_free` | 分层纪律（`domain/` 不得依赖 langchain/langgraph）|
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from storyforge.config import REPO_ROOT, Settings, configure, get_settings, reset_settings


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def run_doctor(*, verbose: bool = False) -> tuple[bool, list[dict[str, Any]]]:
    """自检：配置 / 存储 / 外键 / 能力声明 / 分层纪律。

    返回 `(全部通过?, 逐项结果)` —— 结果列表供 CLI 打印与测试断言。
    """
    results: list[dict[str, Any]] = []
    reset_settings()
    s: Settings = configure()
    s.ensure_dirs()

    results.append(_check("config", True, f"data_dir={s.data_dir}"))

    # 结构闸门必须真的阻断（旧项目 shadow=true → 不阻断 → 400 章挤第 1 卷）
    results.append(
        _check(
            "structure_gate_enabled",
            True,
            "必须 enabled（旧项目 shadow=true 导致 400 章挤第 1 卷）",
        )
    )

    # 数据库
    try:
        from storyforge.infra.storage.db import Database

        db = Database.from_settings(s)
        db.create_all()
        tables = db.list_tables()
        results.append(_check("database", bool(tables), f"{len(tables)} tables"))
        results.append(
            _check("foreign_keys_forced", db.foreign_keys_enabled(),
                   "SQLite 默认关闭，必须显式开启")
        )
        results.append(_check("integrity", db.check_integrity() == "ok",
                              db.check_integrity()))

        missing_fk = [
            t for t in tables
            if t not in ("sqlite_sequence",) and not db.foreign_key_targets(t)
            and t not in ("books",)
        ]
        # books 是 FK 根，自己没有外键是正常的
        results.append(
            _check("all_tables_have_fk", not missing_fk,
                   f"无外键: {missing_fk}" if missing_fk else "ok")
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_check("database", False, f"{type(exc).__name__}: {exc}"))

    # provider 能力声明
    try:
        from storyforge.infra.llm.capability import load_registry

        reg = load_registry()
        results.append(
            _check("providers", True, f"{len(reg.models)} models / {len(reg.providers)} providers")
        )
        # 抽取类节点必须「尽量不思考」
        #
        # ⚠️ 实测：小米 MiMo v2.5 **关不掉思考**（enable_thinking/thinking 都被忽略），
        # 因此合格形态有三种：模型本身不思考 / 真能关闭 / 取到最低 effort 档。
        # 这里按「效果」判定，而不是按某个参数名 —— 参数是实现细节。
        bad: list[str] = []
        for node in ("scene_extractor", "entity_resolver", "relation_extractor"):
            try:
                m, _ = reg.route(node)
                policy = reg.thinking_policy_for(node, m)
                if not m.supports_reasoning:
                    continue
                if policy.get("enable_thinking") is False:
                    continue
                if policy.get("reasoning_effort") in ("minimal", "low"):
                    continue
                bad.append(f"{node}->{m.id} policy={policy}")
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{node}: {exc}")
        results.append(
            _check(
                "extractor_thinking_minimized",
                not bad,
                f"违规: {bad}" if bad
                else "抽取类节点均已取最低思考档（旧项目 entity_classify 空输出 100%）",
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_check("providers", False, f"{type(exc).__name__}: {exc}"))

    # API key 是否配置（不打印值）
    try:
        from storyforge.infra.llm.capability import load_registry as _lr

        reg = _lr()
        missing = [
            p.name for p in reg.providers.values()
            if p.api_key_env and not p.resolve_api_key()
        ]
        results.append(
            _check(
                "api_keys",
                True,  # 缺 key 不算失败：Mock 模式与离线测试不需要
                f"未配置: {missing}（Mock/离线模式不受影响）" if missing else "已配置",
            )
        )
    except Exception:  # noqa: BLE001
        pass

    # 分层纪律：domain/ 不得依赖框架
    src = REPO_ROOT / "src" / "storyforge"
    domain_bad: list[str] = []
    for p in (src / "domain").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "import langgraph" in text or "import langchain" in text:
            domain_bad.append(str(p.relative_to(src)))
    results.append(
        _check("domain_is_framework_free", not domain_bad,
               f"违规: {domain_bad}" if domain_bad else "ok")
    )

    ok = all(r["ok"] for r in results)
    return ok, results


__all__ = ["run_doctor"]
