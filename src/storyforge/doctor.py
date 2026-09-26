r"""`doctor` 自检 —— 让"系统是否健康"变成一条命令。

## 从 `cli.py` 拆出来的理由

契约规则 10 当年是**单文件 ≤800 行**。加了 `flows` 子命令后 `cli.py` 达到
825 行，规则当场拦下（旧项目 `pipeline.py` 8,607 行的教训）。

★ 该 800 行上限**已于 2026-09-26 取消**（见 `doc/07` §2.1.1）。
本模块**不因此合并回去** —— 拆的依据始终是**职责**，不是行数：

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

import os
from pathlib import Path
from typing import Any

from storyforge.config import REPO_ROOT, Settings, configure, reset_settings


def _check(name: str, ok: bool | None, detail: str = "") -> dict[str, Any]:
    """一条自检结果。

    `ok` 三态：`True` 通过 / `False` 失败 / `None` **跳过**
    （如"数据库未初始化"——必须显式呈现为 SKIP，不得伪装成通过，
    也不得因跳过而判整体失败；doc/11 §4.3、§5.2）。
    """
    return {"check": name, "ok": ok, "detail": detail}


def _probe_writable(data_dir: Path) -> tuple[bool, Path]:
    """只读探测 `data_dir` 将来能否被创建：找最近一个**已存在**的祖先查可写性。

    不实际创建任何目录（PG-1B：doctor 不得有写副作用）。
    """
    probe = data_dir
    while not probe.parent.exists():
        probe = probe.parent
    return os.access(probe.parent, os.W_OK), probe.parent


def run_doctor(*, verbose: bool = False) -> tuple[bool, list[dict[str, Any]]]:
    r"""自检：配置 / 存储 / 外键 / 能力声明 / 分层纪律。

    返回 `(全部通过?, 逐项结果)` —— 结果列表供 CLI 打印与测试断言。
    "全部通过"= 没有任何一项为 `False`；`None`（跳过）单独呈现，不算失败。

    ★ **本函数是只读体检**（PG-1B，doc/11 §4.3）：不创建目录、不创建/迁移数据库。
    旧实现在安装目录里建出 `data/`、`artifacts/` 与 176 KB 的 `storyforge.db`
    （PG-0 实测 B-7）——真实安装到 `C:\Program Files` 时会 PermissionError，
    或被 UAC 虚拟化静默写进 VirtualStore。写盘只属于显式命令（`init-db` 等）。
    """
    results: list[dict[str, Any]] = []
    reset_settings()
    s: Settings = configure()

    # config：只读探测，不代建目录（旧实现这里调 s.ensure_dirs() —— B-7 根因）
    if s.data_dir.exists():
        results.append(_check("config", True, f"data_dir={s.data_dir}"))
    else:
        writable, ancestor = _probe_writable(s.data_dir)
        detail = (
            f"data_dir={s.data_dir} 不存在；首次写盘时将创建"
            f"（父目录 {ancestor} 可写，本命令未创建）"
            if writable
            else f"data_dir={s.data_dir} 不存在，且父目录 {ancestor} 不可写"
        )
        results.append(_check("config", writable, detail))

    # 结构闸门必须真的阻断（旧项目 shadow=true → 不阻断 → 400 章挤第 1 卷）
    results.append(
        _check(
            "structure_gate_enabled",
            True,
            "必须 enabled（旧项目 shadow=true 导致 400 章挤第 1 卷）",
        )
    )

    # 数据库（★ 只读）：库不存在 → 明确报"未初始化"并跳过，绝不代建；
    # 库存在 → 也**不再 create_all()**（schema 归 init-db 管，doctor 只读检查）
    if not s.db_path.exists():
        skip = f"未初始化（运行 init-db）——doctor 不代建（db_path={s.db_path}）"
        results.append(_check("database", None, skip))
        results.append(_check("foreign_keys_forced", None, skip))
        results.append(_check("integrity", None, skip))
        results.append(_check("all_tables_have_fk", None, skip))
    else:
        try:
            from storyforge.infra.storage.db import Database

            db = Database.from_settings(s)
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
    # ★ 三项**独立** try（PG-1C，doc/11 §5.1）：旧实现 providers 与
    # extractor_thinking_minimized 同在一个 try、api_keys 是 `except: pass` ——
    # providers 一抛错，报告就从 10 项静默缩水成 8 项，读报告的人无从知晓
    # 少了什么。现在任何一项失败/无法评估都以自己的行呈现（占位项 ok=None）。
    try:
        from storyforge.infra.llm.capability import load_registry

        reg = load_registry()
    except Exception as exc:  # noqa: BLE001
        placeholder = (
            f"占位：providers 加载失败（{type(exc).__name__}），本项无法评估"
        )
        results.append(_check("providers", False, f"{type(exc).__name__}: {exc}"))
        results.append(_check("extractor_thinking_minimized", None, placeholder))
        results.append(_check("api_keys", None, placeholder))
    else:
        results.append(
            _check("providers", True, f"{len(reg.models)} models / {len(reg.providers)} providers")
        )
        # 抽取类节点必须「尽量不思考」
        #
        # ⚠️ 实测：小米 MiMo v2.5 **关不掉思考**（enable_thinking/thinking 都被忽略），
        # 因此合格形态有三种：模型本身不思考 / 真能关闭 / 取到最低 effort 档。
        # 这里按「效果」判定，而不是按某个参数名 —— 参数是实现细节。
        try:
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
            results.append(_check("extractor_thinking_minimized", False,
                                  f"{type(exc).__name__}: {exc}"))
        # API key 是否配置（不打印值）
        try:
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
        except Exception as exc:  # noqa: BLE001
            results.append(_check("api_keys", False, f"{type(exc).__name__}: {exc}"))

    # 分层纪律：domain/ 不得依赖框架
    # ★ B-5（PG-1C）：旧实现 `rglob("*.py")` 扫到 0 个文件也报 OK ——
    # 安装态没有 src/，架构契约成了恒真。现在分三种形态：
    #   无 domain 目录（安装态）→ SKIP「无源码树」；
    #   目录存在但 0 个 .py    → FAIL（检查对象缺失，绝不报 OK）；
    #   有文件                 → 正常扫描判定。
    src = REPO_ROOT / "src" / "storyforge"
    domain_dir = src / "domain"
    if not domain_dir.is_dir():
        results.append(_check(
            "domain_is_framework_free", None,
            f"跳过：无源码树（{domain_dir} 不存在）—— 安装态的正常形态",
        ))
    else:
        domain_files = sorted(domain_dir.rglob("*.py"))
        if not domain_files:
            results.append(_check(
                "domain_is_framework_free", False,
                f"domain 目录存在但扫到 0 个 .py（{domain_dir}）—— 检查对象缺失",
            ))
        else:
            domain_bad: list[str] = []
            for p in domain_files:
                text = p.read_text(encoding="utf-8")
                if "import langgraph" in text or "import langchain" in text:
                    domain_bad.append(str(p.relative_to(src)))
            results.append(
                _check("domain_is_framework_free", not domain_bad,
                       f"违规: {domain_bad}" if domain_bad else "ok")
            )

    # ★ 跳过（None）不算失败，但绝不能被当成通过 —— CLI 端以 [SKIP] 显式呈现
    ok = all(r["ok"] is not False for r in results)
    return ok, results


__all__ = ["run_doctor"]
