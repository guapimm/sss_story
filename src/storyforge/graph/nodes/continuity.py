"""章间连续性检查 —— **判"有没有过渡"，而不是"地点名是否相等"**。

## 为什么单独成模块

原先这段逻辑在 `detail_planner.py` 里（当时该文件接近规则 10 的 800 行上限，
**该上限已于 2026-09-26 取消**）。
但真正的理由是**职责**：连续性检查是**纯领域规则**
（输入两个 `ChapterPlan`，输出问题清单），与"调 LLM 生成细纲"不是一回事。

## 本模块防的是什么（实测三轮踩到）

旧项目 `outline_lint.py:121-125` 只做 `exit_state != entry_state` 的
**字符串比对**，无法给出"哪里不一致"。本项目第一版同样如此，后果：

  · **假阳性 100%**（合成对照实测）：同一栋楼换房间、同一驿站换前堂、
    纯缩写（`市公安局` vs `市局`）**全被**判为"地点跳变"
  · 细纲**真的生成了**（LLM 调用成功、`completion=5289`）却被整批丢弃：
    `ok=False plans=0` —— **做对的工作被校验器扔掉了**
  · 模型还**永远无法通过**：prompt 说"换**地图**才算换场"，
    校验器按**地点名**判 → 两边说的不是一件事

## 判定顺序（宽严由收到放，任一成立即放过）

  1. **地点其实没变**（含空值）→ 不算跳变
  2. **本章声明了换场** —— `is_transition` 或 `beat_type == TRANSITION`
     （两个独立信号，任一成立即可）
  3. **两个名字指同一场所** —— 复用 `skeleton_planner._mentions` 容错匹配

## 第 3 条：复用既有匹配器（**刻意不新写第二个**）

6 书回归后 `detail:continuity` 命中 **4 本**（达 D-16 必修阈值）。
21 对真实相邻对里 6 对报警、**4 对是假阳性**：

    书15 铁门关驿丞署·院内 → 铁门关驿丞署·前堂      （同一驿站，不同房间）
    书16 市公安局地下…走廊 → 市局地下…走廊          （市公安局 = 市局，缩写）
    书13 仁华医院…病房     → 仁华医院…病房**外**     （只是门外）
    书12 秦牧的柴房       → 杂役坊柴房区            （共享「柴房」，边界情形）

实测既有 `_mentions`（主名拆分 + LCS≥4）**能正确处理其中 5/6**。
刻意**不新写**一个模糊匹配器：本项目已反复踩到"两处实现漂移"
（枚举冲突 / `_classify` 与 CLI 口径分叉 / e2e 绕过配置）。

⚠️ 唯一判错的那条（`秦牧的柴房` vs `杂役坊柴房区`，LCS=2）**不调阈值** ——
真换场那条（`…医院住院部外` vs `仁华医院法务部…`）的 LCS **同样是 2**
（共享「医院」）。**两者在字符串层面不可区分**：降阈值能修好一条，
必然弄坏另一条同样重要的。该情形交给第 2 条 + 审校层，不在这里硬猜。

（D-16：单书/单条失败不得直接触发"放宽阈值"。取证脚本
`scripts/diag_collect_pairs.py` / `scripts/diag_pairs_verdict.py`。）
"""

from __future__ import annotations

from storyforge.domain.enums import BeatType
from storyforge.domain.outline import ChapterPlan
from storyforge.graph.nodes.skeleton_planner import _mentions


def continuity_issues(prev: ChapterPlan, nxt: ChapterPlan) -> list[str]:
    """相邻两章的连续性检查。返回问题清单（空 = 通过）。"""
    # ① 地点没变 → 无跳变（空地点也放过：缺失不是"跳变"，由别的 lint 管）
    a_loc = (prev.exit_state.location_name or "").strip()
    b_loc = (nxt.entry_state.location_name or "").strip()
    if not a_loc or not b_loc or a_loc == b_loc:
        return []

    # ② 本章声明了换场（两个独立信号：布尔旗 + 节拍类型）
    if nxt.is_transition or nxt.beat_type == BeatType.TRANSITION:
        return []

    # ③ 两个名字指同一场所（复用骨架侧那个已加固两次的匹配器）
    #    双向都试：模型可能把限定语加在任一边（「市公安局…」vs「市局…」）
    if _mentions(b_loc, a_loc) or _mentions(a_loc, b_loc):
        return []

    # 都不成立 → 交给原有检查给出**具体**描述（对修复 prompt 有用）
    return prev.exit_state.continuity_problems(nxt.entry_state)


__all__ = ["continuity_issues"]
