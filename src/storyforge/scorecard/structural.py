"""结构层质量指标（零 LLM、确定性）。

字面层（`literal.py`）看**文本表面**，结构层看**文本与规划的一致性**：
细纲是否落实、钩子是否落地、状态是否矛盾、有无无依据新名词。

## 旧项目的实测缺口（本模块要补的）

| 指标 | 旧项目状态 |
|---|---|
| 细纲落实率 | **无机械度量**。`detail_review.py`（949 行 + 7,042 字符 prompt）**整个模块零调用方** |
| 章末钩子落地 | 只靠 Editor 语义判断，无确定性校验 |
| 状态一致性 | `entry_state` 是自由文本，填充率 **仅 13%**，无法机械比对 |
| 无依据新名词 | `entities.check_intro_validity` 有实现但**大量误报**（`石头`(8) `院落`(5) `老者`(13) `动作`(6) 被当专名）（`docs/TODO_20260913.md` §7.2） |

## ⚠️ 本模块**不做**语义判断

「无推进重复（注水）」经旧项目**四次独立验证**确认在字面/结构层面**不可测**
（详见 `literal.py` 模块 docstring）。本模块只做**可机械判定**的比对，
语义判断一律留给 LLM 审校。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from storyforge.domain.outline import ChapterPlan, WorldStateSnapshot

#: 场景「结果」的语义标记词 —— 用于检测细纲场景是否被落实
#: （细纲格式：「场景/人物：目标→阻碍→结果」）
_OUTCOME_MARKERS = ("→", "->", "结果", "最后", "终于", "于是", "因而", "所以")

#: 专名候选的**排除词**（旧项目实测的误报源）
#:
#: 旧项目 `entities.check_intro_validity` 把 `石头`(8) `院落`(5) `木棚`(4) `老者`(13)
#: `动作`(6) `蒲扇`(6) `鼾声`(6) `锁孔`(6) `油灯`(8) `光晕`(6) `石门`(5) `线条`(4)
#: `灯光`(12) `地面`(6) `底层`(5) `区域`(5) `碎石`(4) `记录`(4) `内腑`(4) `书架`(14)
#: `光线`(7) `基础`(6) `重塑`(7) `视野`(3) `藤蔓`(9) `巡夜`(8) `栅栏`(6) `藏身`(5)
#: 全部当成「无依据新名词」→ **噪声淹没真命中**（`docs/TODO_20260913.md` §7.2）。
#:
#: 本模块用**后缀白名单** + **量词/身体部位/普通名词词表**过滤，
#: 只保留真正像专名的候选。
_COMMON_NOUN_SUFFIXES: tuple[str, ...] = (
    "声", "光", "影", "色", "味", "气", "感", "度", "性", "力",
    "面", "边", "处", "间", "上", "下", "里", "外", "中", "内",
    "子", "头", "儿", "们", "者", "员", "家", "手", "口", "眼",
)
_COMMON_NOUNS: frozenset[str] = frozenset(
    {
        "石头", "院落", "木棚", "老者", "动作", "蒲扇", "鼾声", "锁孔", "油灯",
        "光晕", "石门", "线条", "灯光", "地面", "底层", "区域", "碎石", "记录",
        "内腑", "书架", "光线", "基础", "重塑", "视野", "藤蔓", "巡夜", "栅栏",
        "藏身", "空气", "声音", "影子", "事情", "东西", "地方", "时候", "样子",
        "办法", "问题", "结果", "意思", "感觉", "神色", "目光", "脚步", "身影",
    }
)
_BODY_PARTS: frozenset[str] = frozenset(
    {
        "左肩", "右肩", "后腰", "前胸", "后背", "手腕", "脚踝", "膝盖", "额头",
        "胸口", "喉咙", "腹部", "肋骨", "眼角", "嘴角", "眉心", "太阳穴",
    }
)


@dataclass
class StructuralMetrics:
    """一章的结构层指标。"""

    #: 细纲场景落实率（0~1）。`None` = 无可比对细纲（缺省即不通过）
    outline_adherence: float | None = None
    #: 章末是否落在规划钩子上
    hook_landed: bool | None = None
    #: 状态一致性违规
    state_violations: list[str] = field(default_factory=list)
    #: 无引入依据的新名词（已过滤误报）
    unsupported_entities: list[str] = field(default_factory=list)
    #: 章首是否承接上一章出口状态
    continues_previous: bool | None = None
    #: 细纲场景数 / 检出落实的场景数
    scenes_planned: int = 0
    scenes_detected: int = 0

    @property
    def has_state_violation(self) -> bool:
        return bool(self.state_violations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outline_adherence": self.outline_adherence,
            "hook_landed": self.hook_landed,
            "state_violations": list(self.state_violations),
            "unsupported_entities": list(self.unsupported_entities),
            "continues_previous": self.continues_previous,
            "scenes_planned": self.scenes_planned,
            "scenes_detected": self.scenes_detected,
        }


# --------------------------------------------------------------------------- #
# 细纲落实率
# --------------------------------------------------------------------------- #


def _normalize_for_match(s: str) -> str:
    """比对前归一：去空白与常见标点（保留汉字与数字）。"""
    return re.sub(r"[\s，。、；：！？…—\-—\"'“”‘’（）()《》【】]+", "", s or "")


def _char_ngrams(s: str, n: int = 2) -> set[str]:
    """字符 n-gram 集合（默认 2-gram）。

    ## 为什么用字符 n-gram 而不是"关键词精确匹配"

    初版实现用「抽取长关键词 + 要求精确出现」，实测**全部失败**：

        细纲 goal:  "陈砚查阅值班登记簿"
        正文原句:   "陈砚翻开值班登记簿，仔细查阅每一页"
        → "陈砚查阅值班登记簿" 不出现 → 落实率误判为 0

    正文从不复述细纲原文，它会**换语序、插修饰**。字符 2-gram **包含率**
    天然容忍这种改写：细纲的 2-gram 大部分仍会出现在正文里。

    不用分词器（避免引入依赖），也不用 4-gram（颗粒太细，改写后重叠骤降）。
    """
    if not s or len(s) < n:
        return set()
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def _containment(needle: str, haystack: str, *, n: int = 2) -> float:
    """`needle` 的字符 n-gram 有多大比例出现在 `haystack` 中（0~1）。

    这是**包含率**而非 Jaccard —— 正文通常比细纲长得多，
    Jaccard 会被长度差稀释（`|A∩B| / |A∪B|`），包含率才反映"细纲内容是否被写到"。
    """
    a = _char_ngrams(_normalize_for_match(needle), n)
    if not a:
        return 0.0
    b = _char_ngrams(_normalize_for_match(haystack), n)
    if not b:
        return 0.0
    return len(a & b) / len(a)


def measure_outline_adherence(
    prose: str, plan: ChapterPlan, *, threshold: float = 0.25
) -> tuple[float | None, int, int]:
    """度量细纲场景落实率。

    返回 `(adherence, scenes_planned, scenes_detected)`。

    ## 方法

    对每个 `SceneBeat`，取 `goal + outcome` 的**字符 2-gram 包含率**：
    细纲语句的 2-gram 有多大比例出现在正文里。超过 `threshold` 即认为该场景被落实。

    ## 为什么不用"关键词精确匹配"

    初版实现失败（实测）：
        细纲: "陈砚查阅值班登记簿"
        正文: "陈砚翻开值班登记簿，仔细查阅每一页"
        → 精确匹配不成立 → 落实率误判为 **0**
    正文从不复述细纲原文，它换语序、插修饰。2-gram 包含率天然容忍改写。

    ## 参数的取舍

    `threshold = 0.25` 是**保守值**：宁可漏判"已落实"（误报需人工复核），
    也不要误判"未落实"（会触发无谓的整章重写）。调低到 0.15 会开始误伤。

    `adherence` 为 `None` 当细纲无场景（无可比对对象 → 缺省即不通过）。
    """
    if not plan.scenes:
        return None, 0, 0
    if not (prose or "").strip():
        return None, len(plan.scenes), 0

    detected = 0
    for scene in plan.scenes:
        source = f"{scene.goal} {scene.outcome}".strip()
        if not source:
            # 场景无 goal/outcome → 视为未落实（细纲本身不合格）
            continue
        if _containment(source, prose) >= threshold:
            detected += 1
    return detected / len(plan.scenes), len(plan.scenes), detected


# --------------------------------------------------------------------------- #
# 章末钩子落地
# --------------------------------------------------------------------------- #


def measure_hook_landed(prose: str, plan: ChapterPlan, *, window: int = 400) -> bool | None:
    """章末是否落在规划的 `end_hook` 上。

    方法：取钩子的字符 2-gram，检查正文**末段**（`window` 字）的包含率是否 ≥0.3。
    无 `end_hook` → `None`（无可比对对象）。

    旧项目对照：Editor 反复判「章末未落在规划钩子上」，而
    **没有任何确定性校验**能提前发现 —— 只能在审校时靠 LLM 语义判断。
    （`docs/TODO_20260913.md` §7.1：第 1-7 章 Editor 判「章末钩子落空」。）
    """
    hook = (plan.end_hook or "").strip()
    if not hook:
        return None
    tail = (prose or "")[-window:]
    if not tail.strip():
        return False
    return _containment(hook, tail) >= 0.3


#: 高频虚词/助词 —— 用于在计算"动作包含率"时剔除，避免"名字撞巧"抬高分数
_FUNCTION_CHARS: frozenset[str] = frozenset(
    "的了着过是在有和与及或但而就都也很将把被给对从向到之其所以并且因为"
    "这那其此该每个一二三四五六七八九十"
)


def _action_grams(text: str, *, n: int = 2) -> set[str]:
    """只保留**实义**字符的 n-gram（剔除虚词与数字）。

    ## 为什么需要这个

    桥接检测初版只做整体 2-gram 包含率，实测**假阴性**：

        上一章未收束事件: "陈砚拒绝给李巡长开门，两人隔门对峙"
        本章开头:         "第二天早上，陈砚在镇公所与李巡长同席吃饭"
        → 整体包含率 = 0.2（恰好等于阈值）→ **判为已承接**
        → 但交集只有 {'陈砚','李巡','巡长'} 三个**人名片段**，
          核心动作（拒绝/开门/隔门/对峙）**全部缺失**

    人名在同一部小说里必然反复出现，"名字撞巧"会让任何包含率指标失真。
    本函数剔除虚词后，剩下的 2-gram 主要由**动作与实义名词**构成，
    才真正反映"这一拍有没有被承接"。
    """
    s = _normalize_for_match(text)
    if not s:
        return set()
    keep = [c for c in s if c not in _FUNCTION_CHARS]
    cleaned = "".join(keep)
    return _char_ngrams(cleaned, n)


def _action_containment(needle: str, haystack: str, *, n: int = 2) -> float:
    """实义 2-gram 的包含率（剔除虚词后）。"""
    a = _action_grams(needle, n=n)
    if not a:
        return 0.0
    b = _action_grams(haystack, n=n)
    if not b:
        return 0.0
    return len(a & b) / len(a)


def check_state_consistency(
    prose: str,
    plan: ChapterPlan,
    *,
    prev_exit: WorldStateSnapshot | None = None,
    bridge_threshold: float = 0.35,
) -> list[str]:
    """状态一致性检查（确定性可判定部分）。

    检查项：
      1. 章首是否承接上一章的未收束事件（**实义** 2-gram 包含率 ≥ `bridge_threshold`）
      2. 章首是否为空（无法承接）

    阈值说明：`0.35` 高于裸包含率的直觉值，因为这里用**剔除虚词后**的实义
    2-gram —— 分母变小、且已排除"名字撞巧"，所以需要更高的重叠才算真承接。

    ⚠️ **不做**：不检查"伤势是否推进"（需与 `injuries` 表比对，
    由 `continuity_checker` 在 P3 接 DB 后做）；不判断语义矛盾。
    """
    violations: list[str] = []

    if prev_exit is not None and prev_exit.open_thread:
        head = (prose or "")[:300]
        if not head.strip():
            violations.append("章首为空，无法承接上一章的未收束事件")
        elif _action_containment(prev_exit.open_thread, head) < bridge_threshold:
            violations.append(
                f"上一章留下未收束事件「{prev_exit.open_thread[:30]}」，"
                f"但本章开头未见承接（旧项目实测：章间跳变是 Editor 低分主因）"
            )
    return violations


# --------------------------------------------------------------------------- #
# 无依据新名词（带误报过滤）
# --------------------------------------------------------------------------- #


#: 专名后缀（真专名的强信号）——宗门/城/谷/殿/阁/塔/山/域/岛/海/林/村/镇/国/府/宫/院/族/堂/楼
_PROPER_NOUN_SUFFIXES: tuple[str, ...] = (
    "宗", "门", "派", "教", "会", "帮", "盟", "殿", "阁", "塔", "楼", "堂", "院",
    "城", "镇", "村", "谷", "渊", "山", "峰", "岭", "海", "岛", "域", "界", "洲",
    "国", "府", "宫", "族", "家", "门", "房", "局", "署", "司", "监", "馆", "所",
)

#: 专名前缀（真专名的强信号）——老/小 + 姓 是旧项目识别的人名模式
_NAME_PREFIXES: tuple[str, ...] = ("老", "小", "阿")

#: 常见中国姓氏（用于 `老X`/`小X` 人名判定）
_SURNAMES: frozenset[str] = frozenset(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮齐康伍余元卜顾孟平黄"
    "和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董"
    "梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡"
    "凌霍虞万支柯昝管卢莫房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉钮龚"
    "程嵇邢滑裴陆荣翁荀羊甄封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山"
)


def _looks_like_proper_noun(token: str) -> bool:
    """启发式判定是否**像**专名。

    ## 旧项目实测的误报（`docs/TODO_20260913.md` §7.2）

        `石头`(8) `院落`(5) `木棚`(4) `老者`(13) `动作`(6) `蒲扇`(6) `鼾声`(6)
        `锁孔`(6) `油灯`(8) `光晕`(6) `石门`(5) `线条`(4) `灯光`(12) `地面`(6)
        `底层`(5) `区域`(5) `碎石`(4) `记录`(4) `内腑`(4) `书架`(14) `光线`(7)
        `基础`(6) `重塑`(7) `视野`(3) `藤蔓`(9) `巡夜`(8) `栅栏`(6) `藏身`(5)

    → **噪声淹没真命中**，让「无依据名词」这条闸门信噪比失真。

    ## 本实现的判定顺序

    1. 命中常用普通名词表 / 身体部位表 → **不是**专名
    2. 以专名后缀结尾（宗/城/谷/殿…）且长度 ≥3 → **是**专名
       （`云岚宗`、`长明楼`、`青云城`）
    3. `老/小/阿` + 姓氏（2~3 字）→ **是**专名（人名）
    4. 以普通名词后缀结尾的 2 字词 → **不是**专名
    5. 其余 ≥3 字且非普通名词语素 → **是**专名（保守，宁可漏报）

    ⚠️ 只做**提示**不做判罚 —— 语义判断留给 LLM。
    """
    if not token or len(token) < 2:
        return False
    if token in _COMMON_NOUNS or token in _BODY_PARTS:
        return False

    # 2. 专名后缀（强信号）
    if len(token) >= 3 and token.endswith(_PROPER_NOUN_SUFFIXES):
        # 排除「这个地方」这类（末字是后缀但整词是普通表达）
        if token not in _COMMON_NOUNS:
            return True

    # 3. 老/小/阿 + 姓 → 人名
    if len(token) in (2, 3) and token[0] in _NAME_PREFIXES and token[1] in _SURNAMES:
        return True

    # 4. 2 字词以普通名词后缀结尾 → 极可能是普通名词
    if len(token) == 2 and token.endswith(_COMMON_NOUN_SUFFIXES):
        return False

    # 5. 保守：≥3 字，且不含普通名词语素
    if len(token) >= 3:
        return not any(token.endswith(s) for s in ("声", "光", "影", "感", "度", "力"))
    return False


def find_unsupported_entities(
    prose: str,
    known_names: set[str],
    *,
    min_occurrences: int = 2,
) -> list[str]:
    """找出正文中出现但**不在已知名单**里的疑似专名。

    ## 候选抽取

    按**标点与常见虚词**切分文本（不引入分词器），
    再对每个片段判断是否像专名。

    ⚠️ 初版用裸正则 `[\\u4e00-\\u9fff]{2,4}` 抽取，实测产生大量
    **跨词边界的假片段**（`他提起青`、`青锋剑在`、`前震动`），
    真专名 `青锋剑` 反而因为被"切开"而漏检。所以必须先按标点切分。

    旧项目 `entities.check_intro_validity` 的实测问题是**误报率过高**
    （见 `_looks_like_proper_noun` 注释），本实现加了两层过滤：
      1. `_looks_like_proper_noun` 排除普通名词/身体部位
      2. `min_occurrences` 要求至少出现 N 次（真专名通常会重复出现）
    """
    if not prose:
        return []
    # 按标点/空白切分，避免跨词边界的假片段
    segments = re.split(r"[，。、；：！？…\s\"'“”‘’（）()《》【】—\-]+", prose)
    from collections import Counter

    counts: Counter = Counter()
    for seg in segments:
        s = seg.strip()
        if not s:
            continue
        # 段内再用常见动词/虚词断点细分，取 2~4 字候选
        for chunk in re.split(r"[的了着过是在有和与及或但而就都也很将把被给对从向到]", s):
            chunk = chunk.strip()
            if 2 <= len(chunk) <= 5 and re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
                counts[chunk] += 1
            elif len(chunk) > 5:
                # 长片段：滑窗取 2~4 字，但只在片段内（不跨标点）
                for L in (4, 3, 2):
                    for i in range(len(chunk) - L + 1):
                        counts[chunk[i : i + L]] += 1

    known = {k.strip() for k in (known_names or set())}
    out: list[str] = []
    for token, n in counts.most_common():
        if n < min_occurrences:
            continue
        if token in known:
            continue
        if not _looks_like_proper_noun(token):
            continue
        out.append(token)
    return out[:20]


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


def analyze_structural(
    prose: str,
    plan: ChapterPlan | None,
    *,
    prev_exit: WorldStateSnapshot | None = None,
    known_names: set[str] | None = None,
) -> StructuralMetrics:
    """结构层度量汇总。`plan` 为 `None` 时结构指标全部为 `None`（缺省即不通过）。"""
    m = StructuralMetrics()
    if plan is None:
        return m

    adherence, planned, detected = measure_outline_adherence(prose, plan)
    m.outline_adherence = adherence
    m.scenes_planned = planned
    m.scenes_detected = detected
    m.hook_landed = measure_hook_landed(prose, plan)
    m.state_violations = check_state_consistency(prose, plan, prev_exit=prev_exit)
    if known_names is not None:
        m.unsupported_entities = find_unsupported_entities(prose, known_names)
    if prev_exit is not None:
        m.continues_previous = not any(
            "未收束事件" in v for v in m.state_violations
        )
    return m


__all__ = [
    "StructuralMetrics",
    "measure_outline_adherence",
    "measure_hook_landed",
    "check_state_consistency",
    "find_unsupported_entities",
    "analyze_structural",
]
