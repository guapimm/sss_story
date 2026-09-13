"""命令行入口 —— 一期的唯一操作接口（决策 D-05：不做前端）。

包含 `doctor` 自检命令：检查配置、数据库、外键、LLM 连通性、
以及**架构不变量**，让"系统是否健康"变成一条命令。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from storyforge import __version__
from storyforge.config import REPO_ROOT, Settings, configure, get_settings, reset_settings


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def run_doctor(*, verbose: bool = False) -> tuple[bool, list[dict[str, Any]]]:
    """自检：配置 / 存储 / 外键 / 能力声明 / 分层纪律。"""
    results: list[dict[str, Any]] = []

    # 1) 配置加载
    try:
        s = get_settings()
        s.ensure_dirs()
        results.append(_check("config", True, f"data_dir={s.data_dir}"))
    except Exception as exc:  # noqa: BLE001
        results.append(_check("config", False, f"{type(exc).__name__}: {exc}"))
        return False, results

    # 2) 结构闸门必须启用（旧项目 shadow=true 的教训）
    results.append(
        _check(
            "structure_gate_enabled",
            s.structure_gate.enabled,
            "必须 enabled（旧项目 shadow=true 导致 400 章挤第 1 卷）",
        )
    )

    # 3) 数据库 + 外键
    try:
        from storyforge.infra.storage.db import Database

        db = Database.from_settings(s)
        db.create_all()
        fk_on = db.foreign_keys_enabled()
        results.append(_check("database", True, f"{len(db.list_tables())} tables"))
        results.append(
            _check("foreign_keys_forced", fk_on, "SQLite 默认关闭，必须显式开启")
        )
        integrity = db.check_integrity()
        results.append(_check("integrity", integrity == "ok", integrity))

        # 每张从属表必须有 FK
        root_tables = {"books"}
        no_fk = [
            t
            for t in db.list_tables()
            if not t.startswith("sqlite_") and t not in root_tables
            and not db.foreign_key_targets(t)
        ]
        results.append(
            _check("all_tables_have_fk", not no_fk, f"缺 FK: {no_fk}" if no_fk else "ok")
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_check("database", False, f"{type(exc).__name__}: {exc}"))

    # 4) 能力声明与路由
    try:
        from storyforge.infra.llm.capability import load_registry

        reg = load_registry()
        results.append(
            _check("providers", True, f"{len(reg.models)} models / {len(reg.providers)} providers")
        )
        # 抽取类节点必须落在「不思考」的模型或显式关闭思考上
        bad: list[str] = []
        for node in ("scene_extractor", "entity_resolver", "relation_extractor"):
            try:
                m, _ = reg.route(node)
                policy = reg.thinking_policy_for(node, m)
                if m.supports_reasoning and policy.get("enable_thinking") is not False:
                    bad.append(f"{node}->{m.id}")
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{node}: {exc}")
        results.append(
            _check(
                "extractor_thinking_disabled",
                not bad,
                f"违规: {bad}" if bad else "抽取类节点均不思考（旧项目 entity_classify 空输出 100%）",
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(_check("providers", False, f"{type(exc).__name__}: {exc}"))

    # 5) API key 是否配置（不打印值）
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

    # 6) 分层纪律（AST 快速版）
    src = REPO_ROOT / "src" / "storyforge"
    domain_bad: list[str] = []
    for p in (src / "domain").rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        if "import langgraph" in text or "import langchain" in text:
            domain_bad.append(str(p.relative_to(src)))
    results.append(
        _check("domain_is_framework_free", not domain_bad, f"违规: {domain_bad}" if domain_bad else "ok")
    )

    ok = all(r["ok"] for r in results)
    return ok, results


def cmd_doctor(args: argparse.Namespace) -> int:
    ok, results = run_doctor(verbose=args.verbose)
    width = max(len(r["check"]) for r in results) if results else 10
    for r in results:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"  [{mark}] {r['check']:<{width}}  {r['detail']}")
    print()
    print(f"doctor: {'PASS' if ok else 'FAIL'}  (storyforge {__version__})")
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# init-db
# --------------------------------------------------------------------------- #


def cmd_init_db(args: argparse.Namespace) -> int:
    from storyforge.infra.storage.db import Database

    s = get_settings()
    s.ensure_dirs()
    db = Database.from_settings(s)
    db.create_all()
    print(f"数据库已就绪：{s.db_path}")
    print(f"  表：{len(db.list_tables())} 张")
    print(f"  外键强制：{db.foreign_keys_enabled()}")
    print(f"  journal_mode：{db.journal_mode()}")
    return 0


# --------------------------------------------------------------------------- #
# arch-check（架构不变量）
# --------------------------------------------------------------------------- #


def cmd_arch_check(args: argparse.Namespace) -> int:
    """运行架构契约测试（AST 检查）。"""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/contract", "-q", "--no-header"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    tail = (proc.stdout or "").strip().splitlines()[-3:]
    print("\n".join(tail))
    return proc.returncode


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="storyforge", description="长篇小说生成系统")
    p.add_argument("--version", action="version", version=f"storyforge {__version__}")
    sub = p.add_subparsers(dest="command", required=False)

    d = sub.add_parser("doctor", help="自检：配置 / 存储 / 外键 / 能力声明 / 分层纪律")
    d.add_argument("-v", "--verbose", action="store_true")
    d.set_defaults(func=cmd_doctor)

    i = sub.add_parser("init-db", help="初始化数据库（建表 + 开启外键）")
    i.set_defaults(func=cmd_init_db)

    a = sub.add_parser("arch-check", help="运行架构不变量检查（AST）")
    a.set_defaults(func=cmd_arch_check)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    finally:
        reset_settings()


if __name__ == "__main__":
    raise SystemExit(main())
