"""节点：单卷骨架生成（P3-3，人工确认点 C-2）。

## 定位

骨架是「**本卷**的故事线结构」——比圣经细、比细纲粗：
  · 圣经：全书的**世界**（人/地/规则/力量体系）
  · **骨架：本卷的线**（core / relationship / opposition / sub arcs + 段落目标 + 角色 + 势力）
  · 细纲：每一章的**场景拍点**

## 指标来自旧项目自己的设计

旧项目 `ultra_structure_config.json` 的指标**设计正确**：

    { "min_arcs": 6, "min_core_arcs": 2, "min_relationship_arcs": 2,
      "min_opposition_arcs": 2, "min_milestones": 9, "min_factions": 4,
      "min_characters": 3, "min_volumes": 3,
      "enabled": false, "shadow": true }     ← ★ 关键在这里

**但它 `enabled: false, shadow: true`** —— 从不阻断。于是书 23 以
「400 章全在第 1 卷、4 个里程碑、0 个势力实体」的状态**顺利写了 400 章**。

> **设计对了却关着的校验器，等于没有。**

本节点的校验器**默认阻断**，并沿用同一组指标（`min_arcs` 等），
只是把它们落到「单卷骨架」这一层。

## 为什么骨架也必须有"扩张维度"

卷级台阶（`volume_planner`）已经规定了本卷的 `new_locations` / `new_factions`。
骨架**必须落实它们** —— 否则台阶只是纸面规划，骨架又回到"一栋楼里"。
因此本节点接收本卷台阶作为输入，并校验骨架里的势力/角色与台阶一致。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from storyforge.domain.outline import VolumePlan
from storyforge.infra.llm.client import CallContext, LLMClient
from storyforge.infra.llm.schemas import SkeletonOut
from storyforge.infra.storage.artifacts import ArtifactStore

logger = logging.getLogger(__name__)


SKELETON_PLANNER_SYSTEM = """你是长篇小说结构师。为**当前这一卷**规划故事线骨架。

## 输出内容

- `arcs`：本卷的故事线（6~10 条）。每条给出 `key`（简短唯一标识）、
  `kind`、`description`（这条线讲什么）、`chapter_span`（本卷内的起止章序，如 [1, 50]）。
  `kind` 取值：
    · `core` —— 主线（**至少 2 条**）
    · `relationship` —— 关系线（**至少 2 条**，讲人与人的关系如何变化）
    · `opposition` —— 对立线（**至少 2 条**，讲与谁对抗、赌注是什么）
    · `sub` —— 支线
- `segments`：把本卷切成若干段，每段一句目标。**每段目标必须两两不同**——
  旧项目实测：模型曾把 6 段目标写成同一句话，导致 29 个重复章节标题覆盖 65/300 章。
- `characters`：本卷主要角色（3~6 个）。
- `factions`：本卷登场势力（4~8 股）。**必须包含卷级台阶指定的新势力**，
  并为每股给出立场与目标。

## 硬性要求

1. **必须落实本卷的横向扩张**：给出的势力必须涵盖台阶指定的新势力；
   故事线的场景必须用到台阶指定的新地图。长篇最常见的失败是**全书困在一个地方**。
2. **关系线不能只有"同门/师徒"**：需要有会变化的张力
   （结盟又反目、表面客气暗中提防、从敌对到并肩）。
3. **对立线要写出赌注**：输了会失去什么（不是"打不过"而是具体代价）。
4. 每条线都要有**在本卷内的起止**，不要写成贯穿全书的概述。

## ★ 字段名必须逐字照抄下面的骨架

实测教训：只用散文描述字段时，模型会自造同义名或改变形状，
而每个不一致都会让整轮失败。

```json
{
  "arcs": [
    {
      "key": "十三条",
      "kind": "core",
      "description": "这条线讲什么（一句话，≥10 字）",
      "chapter_span": [1, 60]
    }
  ],
  "segments": ["第一段的目标", "第二段的目标（必须与上一条不同）"],
  "characters": ["角色甲", "角色乙", "角色丙"],
  "factions": ["势力甲", "势力乙", "势力丙", "势力丁"]
}
```

⚠️ `arcs` 每条都必须有 `key` / `kind` / `description` / `chapter_span` 四项；
`kind` 只能是 `core` / `relationship` / `opposition` / `sub` 之一（英文小写）。
`segments` / `characters` / `factions` 都是**字符串数组**。

**只输出 JSON**，不要任何解释或代码块标记。"""


#: 骨架校验指标（沿用旧项目 `ultra_structure_config.json` 的设计）
MIN_ARCS = 6
MAX_ARCS = 12
MIN_CORE_ARCS = 2
MIN_RELATIONSHIP_ARCS = 2
MIN_OPPOSITION_ARCS = 2
MIN_CHARACTERS = 3
MIN_FACTIONS = 4


@dataclass
class SkeletonResult:
    skeleton: SkeletonOut | None
    skeleton_ref: str
    violations: list[str] = field(default_factory=list)
    llm_calls: int = 0
    ok: bool = False
    error: str | None = None


#: 判定「骨架是否落实了台阶的某一项」时，忽略这些字符。
#:
#: ★ 为什么必须容错（实测假阳性）：校验器原先用**整串精确匹配**
#: （`f in blob`），而模型会自然地改写措辞：
#:
#:     台阶要求                              骨架实际写的
#:     档案馆内部保守派值夜人集团      →     保守派值夜人集团（省了修饰语）
#:     外部调查记者组织"记忆之眼"      →     '记忆之眼'组织（换了引号、调了语序）
#:     城市边缘的废弃印刷厂（关联…）   →     潜入废弃印刷厂（只留关键词）
#:
#: 三处**都落实了**，却被判为"未落实" —— 这是**假阳性**，
#: 而假阳性比漏判更糟：它会逼人去改本来正确的产物，或在反复失败后
#: 干脆把校验器关掉（旧项目 `enabled: false` 的老路）。
#:
#: 这与 P2 修「细纲落实率」时踩的是同一个坑（当时从关键词精确匹配
#: 改成字符 2-gram 包含率），这个校验器当时漏改了。
_STRIP_CHARS = "（）()「」《》\"'‘’“”·—-,，。.、:：;；!！?？ \t\n\r"

#: 「算作提到了」所需的**最长公共子串**长度下限（字符）。
#:
#: 用**绝对长度**而不是比例，因为比例在长短名上行为不一致：
#:   · `城市边缘的废弃印刷厂` vs 骨架里的 `潜入废弃印刷厂`
#:     → LCS = `废弃印刷厂`（5 字），比例只有 0.5，但**显然是落实了**
#:   · `记忆` vs 骨架里的 `'记忆之眼'组织`
#:     → LCS = `记忆`（2 字），比例 1.0，但**算不上落实了「记忆」这个势力**
#:
#: 取 4：中文里 4 字已足够辨识（`记忆之眼` / `废弃印刷厂`），
#: 而 2~3 字的重合多半是巧合（`组织` / `集团` 这类通用词）。
_MENTION_MIN_LCS = 4


def _name_and_gloss(item: str) -> tuple[str, str]:
    """把台阶项拆成 **(主名, 括注)**。

    ## ★ 为什么必须拆（实测第二次假阳性）

    台阶项常写成「主名（解释）」：

        旧派（主张严格恪守现有手册，反对任何调查或改变）
        探疑者（由数名年轻值夜人秘密组成，试图寻找规则漏洞与真相）

    而骨架会用**同样的主名 + 自己的括注**：

        旧派（以老白为代表）
        探疑者（陈默、张小刀等秘密小组）

    这**完全落实了**台阶要求（实体在、线在、段落在）。
    但整串比对时，24 字的长括注把 2 字的 `旧派` 淹没了 ——
    LCS 只有 2，低于阈值 4 → 判为"未落实"。

    **括注是解释，主名才是标识。** 因此匹配时以主名为准；
    骨架只要用了同一个主名就算落实（它的括注可以完全不同）。
    """
    s = (item or "").strip()
    if not s:
        return "", ""
    m = re.match(r"^([^（(]+)[（(](.*)[）)]\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s, ""


def _normalize_for_match(text: str) -> str:
    """去掉全部标点与空白，只留下可比对的字。

    ⚠️ 用 `re.sub` 全局删除，而不是 `str.strip` ——
    `strip` 只处理两端，`外部调查记者组织“记忆之眼”` 的**起始引号在中间**，
    根本不会被去掉（实测踩到）。
    """
    return re.sub(f"[{re.escape(_STRIP_CHARS)}]", "", text or "")


def _mentions(blob: str, item: str) -> bool:
    """骨架文本里是否**落实**了台阶的这一项（容错匹配）。

    判定顺序（由严到宽）：
      1. **主名整串出现** —— 台阶项写作「主名（解释）」时，
         骨架用了同一主名即算落实（它的括注可以不同）
      2. 整串出现（去标点后）
      3. **最长公共子串 ≥ `_MENTION_MIN_LCS`** —— 抓省修饰语/截断的写法

    只做"是否出现"的确定性判断，不做语义判断 ——
    "这个势力是否被安排得合理"属于审校层的事。
    """
    if not item or not item.strip():
        return True
    b = _normalize_for_match(blob)
    name, _gloss = _name_and_gloss(item)
    # ① 主名（这是最可靠的标识）
    if name:
        qn = _normalize_for_match(name)
        # 主名很短（≤3 字）时要求它**独立成词**，避免「旧派」撞上「旧派系」
        if qn and qn in b:
            return True
    # ② 整串
    q = _normalize_for_match(item)
    if not q:
        return True
    if q in b:
        return True
    # ③ 最长公共子串（抓截断/省修饰语）
    return _lcs_len(b, q) >= _MENTION_MIN_LCS


def _lcs_len(a: str, b: str) -> int:
    """最长公共**子串**长度（连续）。

    用子串而不是子序列：子序列会把
    「保守派值夜人集团」与「保守派系另有其人集团」也算匹配 —— 那太宽。
    """
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    best = 0
    for ca in a:
        cur = [0] * (len(b) + 1)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


#: 兼容旧名（早先叫 `_lcs_ratio`，返回比例；比例口径在长短名上不一致，已废弃）
def _arc_core_key(key: str) -> str:
    """剥离线的前缀/尾标，用于**近重复**判定。

    旧项目实测（`docs/AGENTS.md`）：模型补线时会**换前缀/加后缀凑数**
    （`主线·X` → `X主线` → `X（核心）`），精确名去重拦不住。
    这也是旧项目引入 `_arc_core_key` 的原因。
    """
    k = (key or "").strip()
    k = re.sub(r"^(主线|支线|暗线|辅线)[·\.\-—:：]?", "", k)
    k = re.sub(r"[（(](core|主线|支线|暗线|辅线|核心)[)）]$", "", k)
    k = re.sub(r"[·\.\-—:：]+$", "", k)
    return k.strip()


def validate_skeleton(sk: SkeletonOut, volume: VolumePlan | None = None) -> list[str]:
    """零 LLM 校验。返回问题列表（空 = 通过）。

    指标沿用旧项目 `ultra_structure_config.json`（它设计正确但从未启用）。
    """
    issues: list[str] = []
    arcs = list(sk.arcs or [])

    # ── 线的数量与构成 ──
    if len(arcs) < MIN_ARCS:
        issues.append(f"故事线只有 {len(arcs)} 条（要求 ≥{MIN_ARCS}）")
    if len(arcs) > MAX_ARCS:
        issues.append(f"故事线过多（{len(arcs)} > {MAX_ARCS}），会分散主线")

    by_kind: dict[str, list] = {}
    for a in arcs:
        by_kind.setdefault((a.kind or "").strip(), []).append(a)
    checks = (
        ("core", MIN_CORE_ARCS, "主线"),
        ("relationship", MIN_RELATIONSHIP_ARCS, "关系线"),
        ("opposition", MIN_OPPOSITION_ARCS, "对立线"),
    )
    for kind, need, label in checks:
        have = len(by_kind.get(kind, []))
        if have < need:
            issues.append(f"{label}只有 {have} 条（要求 ≥{need}）")

    # ── 近重复线（防模型换前缀凑数）──
    seen: dict[str, str] = {}
    for a in arcs:
        core = _arc_core_key(a.key)
        if not core:
            issues.append(f"故事线 key 为空或无效：{a.key!r}")
            continue
        if core in seen:
            issues.append(
                f"故事线近重复：「{a.key}」与「{seen[core]}」核心名相同"
                f"（模型常用换前缀/加后缀凑数，故按去前缀后的核心名判定）"
            )
        else:
            seen[core] = a.key

    # ── 短描述 ──
    thin = [a.key for a in arcs if len((a.description or "").strip()) < 10]
    if thin:
        issues.append(f"{len(thin)} 条故事线描述过短（<10 字）：{thin[:3]}")

    # ── 角色与势力 ──
    chars = [c for c in (sk.characters or []) if str(c).strip()]
    if len(chars) < MIN_CHARACTERS:
        issues.append(f"主要角色只有 {len(chars)} 个（要求 ≥{MIN_CHARACTERS}）")
    facs = [f for f in (sk.factions or []) if str(f).strip()]
    if len(facs) < MIN_FACTIONS:
        issues.append(
            f"势力只有 {len(facs)} 股（要求 ≥{MIN_FACTIONS}）—— "
            f"旧项目势力实体实测 0 个，长篇需要鲜活的势力格局"
        )

    # ── 段落目标必须两两不同（旧项目实测同句导致 29 个重复标题）──
    segs = [s.strip() for s in (sk.segments or []) if s.strip()]
    if segs:
        dup = {s for s in segs if segs.count(s) > 1}
        if dup:
            issues.append(
                f"段落目标有 {len(dup)} 处逐字重复：{sorted(dup)[:2]} —— "
                f"旧项目实测 6 段同句导致 29 个重复章节标题覆盖 65/300 章"
            )

    # ── 落实卷级台阶的扩张要求（关键）──
    if volume is not None:
        fac_blob = " ".join(facs)
        missing_fac = [
            f for f in volume.new_factions
            if f and not _mentions(fac_blob, f)
        ]
        if missing_fac:
            issues.append(
                f"骨架未落实本卷台阶指定的新势力：{missing_fac} —— "
                f"台阶规定了扩张，骨架必须落地，否则又回到「全书困在一处」"
            )
        loc_blob = " ".join(
            f"{a.description} {a.key}" for a in arcs
        ) + " " + " ".join(segs)
        missing_loc = [
            loc for loc in volume.new_locations
            if loc and not _mentions(loc_blob, loc)
        ]
        if missing_loc:
            issues.append(
                f"骨架的故事线/段落未提及本卷新地图：{missing_loc} —— "
                f"新地图必须在骨架里有对应的线，否则细纲不会安排换场"
            )
    return issues


def persist_skeleton(store: ArtifactStore, book_id: int, volume_no: int,
                     sk: SkeletonOut) -> str:
    ref = ArtifactStore.skeleton_key(book_id, volume_no)
    store.write_json(ref, sk.model_dump())
    return ref.key


def load_skeleton(store: ArtifactStore, book_id: int, volume_no: int) -> SkeletonOut:
    ref = ArtifactStore.skeleton_key(book_id, volume_no)
    return SkeletonOut.model_validate(store.read_json(ref))


def _skeleton_user_prompt(
    *,
    bible_text: str,
    volume: VolumePlan,
    previous_summary: str = "",
    extra_issues: list[str] | None = None,
) -> str:
    """构造骨架的 user prompt（易变部分）。

    ★ 卷级台阶是**必给输入** —— 骨架必须落实台阶的扩张要求。
    """
    parts = [
        bible_text,
        "",
        f"【当前卷】第 {volume.volume_no} 卷《{volume.title}》",
        f"卷目标：{volume.goal}",
        f"本卷章数：{volume.chapter_count}",
        f"核心矛盾：{volume.core_conflict}",
        f"主要对手：{volume.antagonist}",
        f"本卷升级：{volume.escalation_from_prev}",
        "",
        "【本卷必须落实的横向扩张】",
        f"- 新地图（必须在故事线中用到）：{'、'.join(volume.new_locations)}",
        f"- 新势力（必须出现在 factions 里）：{'、'.join(volume.new_factions)}",
    ]
    if volume.milestones:
        pts = "、".join(f"第{m.chapter_no}章 {m.key}" for m in volume.milestones[:12])
        parts += ["", f"【卷内里程碑】{pts}"]
    if previous_summary:
        parts += ["", "【上一卷结束时的局面】", previous_summary]
    parts += ["", "请规划本卷的故事线骨架。"]
    if extra_issues:
        parts += [
            "",
            "【上一次骨架未通过校验，请针对下列问题修正后重新输出完整 JSON】",
            *[f"- {i}" for i in extra_issues],
            "",
            "其余部分保持原样。",
        ]
    return "\n".join(parts)


async def build_skeleton(
    client: LLMClient,
    *,
    book_id: int,
    volume: VolumePlan,
    bible_text: str,
    previous_summary: str = "",
    max_rounds: int = 2,
    idempotency_key: str | None = None,
) -> SkeletonResult:
    """生成本卷骨架（零 LLM 校验失败则带问题清单重试）。

    人工确认点 **C-2**：**首批**骨架完成后应暂停等人工确认
    （决策 D-14：确认后可让后续批次方向正确，同时细纲立即并行开工，不阻塞）。
    """
    if not volume.new_locations or not volume.new_factions:
        return SkeletonResult(
            skeleton=None, skeleton_ref="", ok=False,
            error=(
                f"第 {volume.volume_no} 卷的台阶缺少 new_locations / new_factions —— "
                f"骨架无法落实扩张要求（应先修复台阶）"
            ),
        )

    ctx = CallContext(node="skeleton_planner", book_id=book_id,
                      volume_no=volume.volume_no,
                      idempotency_key=idempotency_key)
    calls = 0
    last: list[str] = []

    for attempt in range(max_rounds + 1):
        user = _skeleton_user_prompt(
            bible_text=bible_text, volume=volume,
            previous_summary=previous_summary,
            extra_issues=last if attempt > 0 else None,
        )
        try:
            sk, call = await client.complete_structured(
                node="skeleton_planner",
                system=SKELETON_PLANNER_SYSTEM,
                user=user,
                schema=SkeletonOut,
                context=ctx,
            )
            if not call.from_cache:
                calls += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("skeleton LLM failed (attempt %d): %s", attempt + 1, exc)
            return SkeletonResult(
                skeleton=None, skeleton_ref="", violations=last,
                llm_calls=calls, ok=False, error=f"骨架生成调用失败：{exc}",
            )

        last = validate_skeleton(sk, volume)
        if not last:
            return SkeletonResult(
                skeleton=sk, skeleton_ref="", violations=[],
                llm_calls=calls, ok=True,
            )
        logger.info("skeleton validation failed (round %d): %s", attempt + 1, last)
        if attempt >= max_rounds:
            break

    return SkeletonResult(
        skeleton=None, skeleton_ref="", violations=last,
        llm_calls=calls, ok=False,
        error=f"骨架在 {max_rounds + 1} 次尝试后仍未通过校验",
    )


__all__ = [
    "SKELETON_PLANNER_SYSTEM",
    "MIN_ARCS",
    "MIN_CORE_ARCS",
    "MIN_RELATIONSHIP_ARCS",
    "MIN_OPPOSITION_ARCS",
    "MIN_CHARACTERS",
    "MIN_FACTIONS",
    "SkeletonResult",
    "validate_skeleton",
    "persist_skeleton",
    "load_skeleton",
    "build_skeleton",
]
