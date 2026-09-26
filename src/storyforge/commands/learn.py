"""learn 侧的命令 —— 流派清单（`flows`）与联网研究（`research`）。

## 为什么单独成模块

契约规则 10 当年是**单文件 ≤800 行**。加完 `research` 后 `cli.py` 达 798 行
（只剩 2 行余量），下一个命令必然撞限。

★ 该 800 行上限**已于 2026-09-26 取消**（见 `doc/07` §2.1.1）。
但本模块**不因此合并回去** —— 拆的依据始终是**职责**，不是行数：

拆的依据是**职责 + 面向的模式**：

    cli.py            —— 命令分发 + 通用命令（doctor/plan/write/score/report…）
    commands/learn.py —— **learn 侧的可视化入口**（本模块）

这两个命令的共同点：都是**零 LLM 或可离线**、都是给用户"看"的
（用户明确要求 learn「全流程可视化，避免黑盒」）。
它们不参与正文生成，坏掉也不会污染产物 —— 与 `plan`/`write` 的
风险等级完全不同，理应分开。

## 可视化是这个模块的存在理由

learn 的目标流程里有两处"用户必须先看到才能决策"：

    流派可多选      → `cmd_flows`（看清单 + 看完整卡）
    知识点够不够    → `cmd_research`（逐轮 query / 累计知识点 / 是否达标）

若没有这两个命令，用户只能看到最终正文 —— 又回到黑盒。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from storyforge.config import get_settings


# --------------------------------------------------------------------------- #
# flows（流派清单 —— learn 的「流派可多选」可视化入口）
# --------------------------------------------------------------------------- #


def cmd_flows(args: argparse.Namespace) -> int:
    """列出/查看流派卡（**零 LLM、零网络**）。

    ## 为什么要有这个命令（用户要求"全流程可视化，避免黑盒"）

    learn 的目标流程第二步是「选研究主题和小说题材，流派**可多选**」——
    用户必须先**看到**可选清单与每张卡的内容，才能做选择。
    否则又是黑盒：系统说"已按悟道流生成"，而用户不知道悟道流是什么。

    两个命名空间（实测两套数据语义不同，见 `modes/flows.py`）：
      · `tropes`   16 个通用叙事套路（story 用，已被题材引用）
      · `xianxia`  25 个修仙流派（learn 用，可多选）
    """
    from storyforge.modes.flows import NAMESPACES, flows_dir, load_flow, load_namespace

    ns = args.namespace
    if ns == "all":
        namespaces = list(NAMESPACES)
    else:
        namespaces = [ns]

    missing = False
    for i, name in enumerate(namespaces):
        if i:
            print()
        # ★ B-8（PG-1C，doc/11 §5.1）：区分「资源缺失」与「命名空间为空」——
        # 数据层 load_namespace 的 {} 语义保持不动（有测试锁定其正当性，
        # 全新 clone 尚未填充时确实是空；doc/11 §2.2），用户可见层在这里大声报错：
        # 安装态资源目录缺失 = wheel 打包/安装不完整，静默打印"目录为空"且退出码 0
        # 会把损坏伪装成正常（PG-0 实测：`flows -n xianxia` 返回 0、0 个流派）。
        d = flows_dir(name)
        if not d.exists():
            print(f"══ {name} ══  {NAMESPACES.get(name, '')}")
            print(
                f"  ★ 资源缺失：{d} 不存在 —— 安装不完整或 wheel 缺资源",
                file=sys.stderr,
            )
            missing = True
            continue
        cards = load_namespace(name)
        print(f"══ {name} ══  {NAMESPACES.get(name, '')}")
        if not cards:
            print("  （目录存在但为空 —— 全新 clone 尚未运行移植脚本时属正常状态）")
            continue
        print(f"  共 {len(cards)} 个\n")
        for fid, c in cards.items():
            # 一行摘要：id / 名 / 卖点
            hook = c.hook[:44] + ("…" if len(c.hook) > 44 else "")
            print(f"  {fid:<24} {c.name:<12} {hook}")

    # --detail：打印指定流派的完整卡（供用户确认）
    if args.detail:
        print()
        for fid in args.detail:
            found = None
            for name in namespaces:
                try:
                    found = load_flow(fid, name)
                    break
                except Exception:  # noqa: BLE001
                    continue
            if found is None:
                print(f"★ 找不到流派 {fid!r}", file=sys.stderr)
                return 2
            print("─" * 76)
            print(f"【{found.namespace}/{found.id}】")
            print("─" * 76)
            print(found.writer_block(include_example=args.with_example))
            print()

    if missing:
        print()
        print("★ 流派资源缺失：请检查安装完整性（见上方 ★ 行）。", file=sys.stderr)
        return 2
    if not args.detail:
        print()
        print("查看某流派完整卡：storyforge flows --detail <id> [--with-example]")
        print("learn 侧流派可多选：传多个 id 即可（顺序即权重顺序）")
    return 0


# --------------------------------------------------------------------------- #
# research（联网研究 —— learn 的「知识点」来源，可离线用 Mock）
# --------------------------------------------------------------------------- #


def cmd_research(args: argparse.Namespace) -> int:
    """按知识点目标做多轮联网研究（**逐轮可视化**）。

    ## 为什么要有这个命令

    用户要求 learn「全流程**可视化**，避免黑盒」。
    研究是流程里最不可见的一步 —— 若不展示"搜了什么、够不够"，
    用户只能看到最终正文，无法判断知识点是否充分。

    本命令把「轮次 / 每轮 query / 累计知识点 / 是否达标」全部打出来。

    ## 离线模式

    未配置 `TAVILY_API_KEY` 时用 Mock（结果为占位文本，**带 `[Mock]` 标记**），
    用于在不花钱的前提下验证编排与可视化。
    """
    from storyforge.infra.retrieval import (
        ResearchCache,
        ResearchError,
        make_research_provider,
        research_topic,
        target_points_for,
    )

    if args.cache_stats:
        st = ResearchCache().stats()
        print("研究缓存")
        for k in ("path", "topics", "fresh", "stale", "ttl_days", "size_bytes"):
            print(f"  {k:<11} {st[k]}")
        return 0

    if not args.topic:
        print("需要 --topic（或用 --cache-stats 看缓存）", file=sys.stderr)
        return 2

    s = get_settings()
    print(f"研究主题：{args.topic}")
    print(f"篇幅档位：{args.scale}    目标知识点：{target_points_for(args.scale)}")
    try:
        provider = make_research_provider(allow_mock=not args.require_real)
    except ResearchError as exc:
        print(f"★ {exc}", file=sys.stderr)
        return 1

    is_mock = provider.__class__.__name__.startswith("Mock")
    if is_mock:
        print("⚠️  当前为 Mock 模式（未配置 TAVILY_API_KEY）——"
              "结果为占位文本，仅用于验证流程与可视化")

    cache = ResearchCache() if not args.no_cache else ResearchCache(
        path=Path(s.data_dir) / "_no_cache.json", ttl_days=0
    )
    print()
    print("研究中 …")
    plan = research_topic(
        args.topic, scale=args.scale, provider=provider, cache=cache,
        resume=not args.no_cache,
    )
    print()
    for line in plan.summary_lines():
        print(line)

    if args.show_snippets:
        print()
        print("检索片段（前 %d 条）" % args.show_snippets)
        for i, sn in enumerate(plan.snippets[: args.show_snippets], 1):
            print(f"  [{i}] {sn.title[:60]}")
            print(f"      {sn.url or '（无 URL —— Mock 占位）'}")
            print(f"      {sn.text[:100]}…")

    return 0 if plan.reached_target else 1


def add_learn_subparsers(sub) -> None:
    """把 learn 侧命令注册到主 parser（供 `cli.build_parser()` 调用）。"""
    from storyforge.commands.learning import cmd_learn_map

    fl = sub.add_parser(
        "flows",
        help="流派清单（learn 的「流派可多选」可视化入口；零 LLM）",
    )
    fl.add_argument(
        "--namespace", "-n", default="all",
        help="命名空间：all / tropes（16 通用套路）/ xianxia（25 修仙流派）",
    )
    fl.add_argument(
        "--detail", "-d", nargs="+", metavar="ID",
        help="打印这些流派的完整卡（可多个，learn 侧即多选）",
    )
    fl.add_argument(
        "--with-example", action="store_true",
        help="完整卡里带上 arc_example（风格示例，规划期用）",
    )
    fl.set_defaults(func=cmd_flows)

    rs = sub.add_parser(
        "research",
        help="联网研究知识点（learn；未配置 key 时用 Mock）",
    )
    rs.add_argument("--topic", "-t", help="研究主题")
    rs.add_argument("--scale", "-s", default="standard",
                    help="篇幅档位：fast/standard/grand/ultra（决定目标知识点数）")
    rs.add_argument("--show-snippets", type=int, default=0, metavar="N",
                    help="展示前 N 条检索片段（看内容而不只看条数）")
    rs.add_argument("--no-cache", action="store_true",
                    help="忽略缓存，强制重新联网")
    rs.add_argument("--require-real", action="store_true",
                    help="必须真联网（无 key 直接失败，不用 Mock）")
    rs.add_argument("--cache-stats", action="store_true",
                    help="只看缓存统计（不研究）")
    rs.set_defaults(func=cmd_research)

    # ── learn-map：学习地图 + 两个确认点（L-1 知识点 / L-2 平替修辞）──
    lm = sub.add_parser(
        "learn-map",
        help="学习地图：生成 / 查看 / **逐条确认** / 编辑 / 回退（learn 的 L-1/L-2）",
    )
    lm.add_argument(
        "action",
        choices=["gen", "show", "accept", "edit", "reject", "rollback", "history",
                 "ledger", "teach", "due"],
        help="gen 生成 / show 查看 / accept 确认 / edit 改 / reject 标重做 / "
             "rollback 回退 / history 历史 / "
             "ledger 教学账本 / teach 记录讲授 / due 查到期复习",
    )
    lm.add_argument("--book-id", type=int, required=True)
    lm.add_argument("--topic", default="", help="研究主题（gen 用）")
    lm.add_argument("--scale", default="standard", help="篇幅档位（gen 用）")
    lm.add_argument("--genre", default="xianxia", help="题材（gen 用，决定种子映射）")
    lm.add_argument("--points", action="store_true", help="accept：确认 L-1 知识点")
    lm.add_argument("--mappings", nargs="*", default=None, metavar="TERM",
                    help="accept：确认 L-2（可只确认指定术语；不带值=全部）")
    lm.add_argument("--term", default="", help="edit/reject：要改的术语")
    lm.add_argument("--replacement", default="", help="edit：新的世界内说法")
    lm.add_argument("--revision", type=int, default=0, help="rollback：回退到第几版")
    lm.add_argument("--force", action="store_true", help="accept：跳过校验")
    lm.add_argument("--offline", action="store_true",
                    help="gen：强制离线路径（种子 + 启发式切分，不调 LLM）")
    lm.add_argument("--verbose", "-v", action="store_true",
                    help="show/ledger：多打明细")
    # ── 阶段 2-4：教学账本（艾宾浩斯）──
    lm.add_argument("--chapter", type=int, default=0,
                    help="teach/due：章号（**全书递增号**，D-15）")
    lm.add_argument("--point-ids", nargs="*", default=None, metavar="ID",
                    help="teach：本章讲授的知识点 id")
    lm.add_argument("--beat", default="",
                    help="due：本章节拍（boss/climax/emotion 不注入复习）")
    lm.add_argument("--has-new-points", action="store_true",
                    help="due：本章有新知识点（复习上限降为 1）")
    lm.set_defaults(func=cmd_learn_map)


__all__ = ["cmd_flows", "cmd_research", "add_learn_subparsers"]
