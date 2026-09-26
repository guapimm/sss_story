"""命令实现 —— 按**面向的模式/职责**分组（`cli.py` 只做分发）。

拆分的直接触发点是契约规则 10（单文件 ≤800 行，**该上限已于 2026-09-26 取消**）：
加完 `research` 后 `cli.py` 达 798 行，只剩 2 行余量。

但拆分的**依据是职责**，不是凑行数：

    cli.py              命令分发 + 通用命令（doctor/plan/write/score/report…）
    commands/learn.py   learn 侧的可视化入口（flows / research）

后者都是**零 LLM 或可离线**、都只给用户"看"，不参与正文生成 ——
坏掉不会污染产物，与 `plan`/`write` 的风险等级不同，理应分开。
"""

from storyforge.commands.learn import (
    add_learn_subparsers,
    cmd_flows,
    cmd_research,
)
from storyforge.commands.learning import cmd_learn_map

__all__ = [
    "add_learn_subparsers",
    "cmd_flows",
    "cmd_research",
    "cmd_learn_map",
]
