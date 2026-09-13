"""字面层质量指标（零 LLM、零 I/O、确定性）。

## 口径继承（**不得随意改动**）

本模块的算法口径**逐项继承旧项目 `app/prose_style.py`**，
因为那些口径是**用书 23 的 47 章 baseline 反解并对齐**过的。
改口径 → baseline 失效 → 无法做新旧对比。

| 指标 | 口径 | 旧项目书 23 实测 |
|---|---|---|
| `intra_redundancy` | `1 - 去重4gram数 / 4gram总数`（章内） | p50 6.21% / max 12.23% |
| `cross_chapter_overlap` | `len(A∩B) / len(A)`，A=本章 4-gram **集合** | p50 9.85% / max 14.51% |
| `dialogue_ratio` | `[“"「『]([^”"」』]{2,})[”"」』]` 内字符 / `len(text.strip())` | p50 10.58% / min 1.47% / max 59.94% |
| `triple_parallelism` | 三段式排比 | 60 处 / 33 章 |
| `chars` | 去空白后长度 | p50 5065 |

### 三条关键的口径细节（踩过坑的）

1. **4-gram 必须在「去空白但保留标点」的文本上切分**。
   去掉标点会让章内冗余率从 6.2% 掉到 **4.2%**，与 baseline 不符
   （`了一下。`、`。”陈砚` 这类**带标点**的填充片段才是有意义的信号）。
2. **跨章重叠用集合而非词频**。词频口径给出 11.7%，与 baseline 9.85% 不符。
3. **单章「前 K」用 `Counter.most_common`**（并列时取首次出现顺序）。
   换成字典序会得到 9/10/4/4 而不是 baseline 的 11/7/6/6。

### 「缺省即不通过」

无法评估的一律返回 `None`（**绝不返回会造成误判的 0**）：
没有 `prev_texts` → `cross_chapter_overlap=None`；
空文本 → `dialogue_ratio=None`、`intra_redundancy=None`。

### ⚠️ 本模块**不测**「无推进重复（注水）」

旧项目做了**四次独立验证，全部失败**（`prose_style.py:51-68`）：

| 尝试 | 方法 | 结果 |
|---|---|---|
| 1 | 段落级 Dice（字面复制） | 全 47 章最高仅 **0.519**；`>=0.60` 章数 **0/47** |
| 2 | 句子级重复 | 第 13 章 177 句仅 1 种重复句；第 2、31 章为 **0** |
| 3 | 「无新增内容单元」段落占比 | 实测 p50 = **0.0%** —— 一本被评为「非常注水」的书得到「零注水」 |
| 4 | 低阈值段级相似度探针 | threshold 0.50 → **0 对**；合成对照在 0.30 下**命中 0** |

**根本原因**：同一拍会**换一批词**来写，且注水段落**一直在引入新道具与编号**。
→ **不得为「注水」建确定性闸门**（那会是一台假阴性机器）。
该问题只能靠 LLM 语义审校 + 人工盲读。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from storyforge.domain.enums import EndingPattern

# --------------------------------------------------------------------------- #
# 精确口径的常量（继承旧项目，不得随意改）
# --------------------------------------------------------------------------- #

#: 空白归一：删除所有空白，**保留标点**
_WS_RE = re.compile(r"\s+")

#: 对话提取：与旧项目 `style_metrics.py` / `prose_style.py` 同口径
_DIALOGUE_RE = re.compile(r"[“\"「『]([^”\"」』]{2,})[”\"」』]")

#: 旧项目的套话表（`style_metrics.py:CLICHES`，逐条抄录）
CLICHES: tuple[str, ...] = (
    # 动作类
    "仿佛", "瞬间", "不禁", "随即", "旋即", "霎时", "顿时", "下意识",
    "不由自主", "心头一紧", "时间仿佛静止", "这一刻",
    # 情感类
    "一丝", "涌上心头", "心中一凛", "心中暗道",
    "无法言喻", "难以言表", "他知道", "深深地", "百感交集",
    # 环境/氛围类
    "空气中弥漫", "淡淡的", "在这个世界里", "在这片大陆上",
    # 外貌/表情类
    "目光如炬", "嘴角勾起", "嘴角微微上扬", "眼中闪过一丝精光",
    "薄唇微启", "眼眸中", "微微一笑", "淡淡说道", "冷哼一声",
    "一抹笑意", "一抹弧度", "他的眼中",
    # 评价/判断类
    "当真是", "竟然是", "不愧是", "不仅如此",
    # 句式/鸡汤类
    "破茧成蝶", "绽放出属于自己", "不禁微微", "命运的齿轮",
    "新的篇章", "新的开始", "属于他的时代", "从此以后",
    "总而言之", "综上所述", "从这一天起",
)

#: 章末「总结式收尾」（情绪/主题收口）—— 逐条抄录旧项目 `_SUMMARY`
SUMMARY_ENDINGS: tuple[str, ...] = (
    "终于", "放下心来", "松了一口气", "如释重负", "尘埃落定", "归于平静", "释然",
    "明白了", "懂得了", "成长", "从这一天起", "从此以后", "新的开始", "新的篇章",
    "他知道", "她知道", "命运", "这一夜", "属于他的", "一切都结束", "一切都值得",
    "值得了", "心里踏实", "安稳下来", "平静下来", "想通了", "不再是",
)

#: 时间流逝（章末收尾即「章末不结束」违规）——
#: 句首时间状语是**强信号**，句尾出现是弱信号。逐条抄录旧项目 `_TIME_HEAD`/`_TIME_TAIL`
TIME_HEAD: tuple[str, ...] = (
    "次日", "翌日", "第二天", "第三天", "三日后", "数日后", "几天后", "一周后", "半个月后",
    "一个月后", "半年后", "多年后", "第二天清晨", "次日清晨", "天亮", "天光大亮", "夜幕降临",
    "当天夜里", "入夜", "深夜", "黄昏", "黎明", "清晨", "傍晚", "一觉醒来", "醒来时",
    "与此同时", "这一夜过去", "转眼", "一晃", "不知不觉", "日子一天天", "时间过得",
)
TIME_TAIL: tuple[str, ...] = (
    "天亮了", "一夜过去", "三天过去", "日子过去", "时间流逝", "已是第二天", "已是次日",
    "到了第二天", "到了次日", "新的清晨", "又一个清晨", "又是一天",
)

#: 预兆式 / 无主暗示贴片假钩子 —— 逐条抄录旧项目 `_OMEN`
OPEN_TEASER_ENDINGS: tuple[str, ...] = (
    "仿佛有什么", "似乎有什么", "好像有什么", "像有什么", "像是有什么", "像什么东西",
    "有什么东西", "什么东西在", "什么东西，", "莫名的", "莫名地", "隐隐", "预感",
    "悄无声息", "悄然", "无人知道", "没人知道", "谁也没有", "无人注意", "没人注意",
    "暗处", "阴影里", "一闪", "一闪而过", "暗中", "窥视", "窥伺", "注视", "盯着",
    "蠢蠢欲动", "暗流", "山雨欲来", "风暴", "正在酝酿", "慢慢往外", "往外顶",
)

#: 动作动词收束（仍是推进拍，不是总结/预兆/时间跳跃）—— 抄录旧项目 `_ACTION_TAIL`
ACTION_TAIL: tuple[str, ...] = (
    "冲", "扑", "抓", "抬", "推", "拉", "转身", "迈步", "踏", "握", "收紧", "退", "迎",
    "递", "举", "挡", "闪", "拔出", "挥", "砸", "踢", "开口", "张口", "喊", "叫", "问",
    "追", "跑", "走", "站起", "起身", "坐下", "低头", "抬头", "望", "看", "盯", "笑",
    "皱", "咬牙", "捏", "拍", "按", "拧", "敲", "掀", "翻", "抽出", "收起", "迈", "跨",
    "俯身", "弯腰", "伸手", "抬手", "点头", "摇头", "转身就走", "扔", "甩", "掷",
    # 收束在「可观察动作/状态」上的拍（仍是推进拍）
    "落", "停", "开", "关", "亮", "灭", "响", "动", "颤", "抖", "晃", "转", "伸", "收",
    "压", "攥", "碰", "贴", "遮", "移", "挪", "进", "蹲", "弯", "直", "回",
)

#: 章首时间/场景硬切（违反「章首承接」）—— 抄录旧项目 `BRIDGE_BREAK_WORDS`
BRIDGE_BREAK_WORDS: tuple[str, ...] = (
    "次日", "第二天", "三天后", "数日后", "与此同时", "画面一转",
    "一个月后", "一段时间后", "翌日", "此后的日子",
)

#: 三段式排比的**分句**切分（与旧项目 `_CLAUSE_SPLIT_RE` 完全一致）
_CLAUSE_SPLIT_RE = re.compile(r"[，,、；;。！？!?…\n“”\"「」『』：:]+")
_PARALLEL_MIN_CLAUSE = 2

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

#: 强情绪词（旧项目要求「每章 ≤2 次」，其余用差异化行为呈现）
STRONG_EMOTION_WORDS: tuple[str, ...] = (
    "悲伤", "心如刀绞", "悲痛欲绝", "怒不可遏", "欣喜若狂",
    "恐惧万分", "绝望", "痛不欲生", "五味杂陈", "百感交集",
)


def _bigrams(s: str) -> set[str]:
    return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def _dice(a: str, b: str) -> float:
    """字符 2-gram Dice 相似度（与旧项目 `_dice` 同口径）。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) < 2 or len(b) < 2:
        return 0.0
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2 * len(ba & bb) / (len(ba) + len(bb))


def _has_cjk(s: str) -> bool:
    return bool(_CJK_RE.search(s))


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT_RE.split(norm_whitespace(text)) if c.strip()]


def _parallel_pair(a: str, b: str) -> bool:
    """两个分句是否「同构」——与旧项目 `_parallel_pair` 完全一致：

    完全相同 / 同头 2 字 / 同尾 2 字 / 2-gram Dice ≥ 0.5（且长度差 ≤2）。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    if len(a) >= 2 and len(b) >= 2 and a[:2] == b[:2]:
        return True
    if len(a) >= 2 and len(b) >= 2 and a[-2:] == b[-2:]:
        return True
    if len(a) >= 3 and len(b) >= 3 and abs(len(a) - len(b)) <= 2 and _dice(a, b) >= 0.5:
        return True
    return False

#: 比喻/明喻标记（用户痛点 3「比喻太多」）
#: ⚠️ 旧项目代码层**零实现**，但 Editor（LLM）在 issue 文本里提到「比喻」**43 次**
#: 并给出了改写建议 —— 说明检测能力不缺，缺的是执行通道。
#: 本模块补上字面层检测，作为**触发器**（不是终判）。
_SIMILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"像[^，。；！？]{1,20}(?:一样|似的|般)"),
    re.compile(r"仿佛[^，。；！？]{1,20}(?:一样|似的|一般)"),
    re.compile(r"如同[^，。；！？]{1,20}(?:一样|似的|一般)"),
    re.compile(r"好似[^，。；！？]{1,20}(?:一样|似的|一般)"),
    re.compile(r"宛如[^，。；！？]{1,20}"),
    re.compile(r"犹如[^，。；！？]{1,20}"),
    re.compile(r"恰似[^，。；！？]{1,20}"),
    re.compile(r"犹如[^，。；！？]{1,20}"),
)

#: 强情绪词（旧项目要求「每章 ≤2 次」，其余用差异化行为呈现）
STRONG_EMOTION_WORDS: tuple[str, ...] = (
    "悲伤", "心如刀绞", "悲痛欲绝", "怒不可遏", "欣喜若狂",
    "恐惧万分", "绝望", "痛不欲生", "五味杂陈", "百感交集",
)


def norm_whitespace(text: str) -> str:
    """空白归一：删除所有空白，**保留标点**。

    ⚠️ 保留标点是刻意的 —— 去标点会让章内冗余率从 6.2% 掉到 4.2%，
    与 baseline 不符（`prose_style.py:26-27` 明确记载）。
    """
    return _WS_RE.sub("", text or "")


def grams(s: str, n: int) -> list[str]:
    """切 n-gram。s 应已做空白归一。"""
    if not s or len(s) < n:
        return []
    return [s[i : i + n] for i in range(len(s) - n + 1)]


# --------------------------------------------------------------------------- #
# 单项指标
# --------------------------------------------------------------------------- #


def intra_redundancy(text: str, *, n: int = 4) -> float | None:
    """章内 4-gram 冗余率 = `1 - 去重数 / 总数`。

    旧项目书 23 实测：p50 **6.21%** / max 12.23%。
    空文本 → `None`（缺省即不通过，不是 0）。
    """
    gs = grams(norm_whitespace(text), n)
    if not gs:
        return None
    return 1.0 - len(set(gs)) / len(gs)


def cross_chapter_overlap(text: str, prev_texts: list[str], *, n: int = 4) -> float | None:
    """跨章重叠 = `len(A ∩ B) / len(A)`。

    A = 本章 4-gram **集合**；B = `prev_texts` 各章 4-gram 集合的**并集**。

    ⚠️ 必须用**集合**。旧项目实测：词频口径给出 11.7%，与 baseline 9.85% 不符。
    无 `prev_texts` → `None`（缺省即不通过）。
    旧项目书 23 实测：p50 **9.85%** / max 14.51%。
    """
    if not prev_texts:
        return None
    cur = set(grams(norm_whitespace(text), n))
    if not cur:
        return None
    prev_union: set[str] = set()
    for t in prev_texts:
        prev_union |= set(grams(norm_whitespace(t), n))
    if not prev_union:
        return None
    return len(cur & prev_union) / len(cur)


def dialogue_ratio(text: str) -> float | None:
    """对话占比 = 引号内字符数 / 文本长度（strip 后）。

    口径与旧项目完全一致：`[“"「『]([^”"」』]{2,})[”"」』]`
    旧项目书 23 实测：p50 **10.58%** / min 1.47% / max 59.94%。
    空文本 → `None`。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    return sum(len(q) for q in _DIALOGUE_RE.findall(raw)) / len(raw)


def triple_parallelism_count(text: str) -> int:
    """连续 3 段式排比数量。

    判据（与旧项目 `triple_parallelism` 完全一致）：
    连续 **≥3** 个分句两两「同构」（完全相同 / 同头 2 字 / 同尾 2 字 /
    2-gram Dice ≥ 0.5），分句按 `，、；。！？…` 与引号换行切分。
    `一跳、一跳、一跳` 这类**同词连排**必然命中。

    旧项目书 23 实测：**60 处 / 33 章**。
    """
    raw = text or ""
    if not raw.strip():
        return 0
    clauses = _clauses(raw)
    if len(clauses) < 3:
        return 0
    count = 0
    seen: set[str] = set()
    i = 0
    while i < len(clauses):
        if len(clauses[i]) < _PARALLEL_MIN_CLAUSE or not _has_cjk(clauses[i]):
            i += 1
            continue
        k = 1
        while (
            i + k < len(clauses)
            and _has_cjk(clauses[i + k])
            and _parallel_pair(clauses[i], clauses[i + k])
        ):
            k += 1
        if k >= 3:
            key = "|".join(clauses[i : i + k])
            if key not in seen:
                seen.add(key)
                count += 1
            i += k
        else:
            i += 1
    return count


def triple_parallelism_samples(text: str, *, limit: int = 60) -> list[str]:
    """三段式排比样本（供人工复核）。

    与 `triple_parallelism_count` 同一遍历逻辑 —— 计数与样本必须同源，
    否则会出现"计数 60 但样本 0 个"的不一致。
    """
    raw = text or ""
    if not raw.strip():
        return []
    clauses = _clauses(raw)
    if len(clauses) < 3:
        return []
    out: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(clauses):
        if len(clauses[i]) < _PARALLEL_MIN_CLAUSE or not _has_cjk(clauses[i]):
            i += 1
            continue
        k = 1
        while (
            i + k < len(clauses)
            and _has_cjk(clauses[i + k])
            and _parallel_pair(clauses[i], clauses[i + k])
        ):
            k += 1
        if k >= 3:
            run = clauses[i : i + k]
            key = "|".join(run)
            if key not in seen:
                seen.add(key)
                out.append("，".join(run)[:limit])
            i += k
        else:
            i += 1
    return out


def _last_nonempty_paragraph(text: str) -> str:
    paras = [p.strip() for p in (text or "").split("\n") if p.strip()]
    return paras[-1] if paras else ""


def classify_ending(text: str, *, window: int = 60) -> EndingPattern:
    """章末收束方式分类（与旧项目 `ending_pattern` 的判据优先级一致）。

    优先级（强结构信号优先）：
      1. `time_skip` —— 末段末句/首句以时间状语开头，或末 `window` 字含时间流逝语
      2. `omen`      —— 末段命中无主暗示/预兆词
      3. `summary`   —— 末段命中情绪/主题收口词
      4. `dialogue`  —— 正文以引号内对白收尾
      5. `mid_action`—— 末句以动作动词收束，或以 `…`/`，`/`——` 未写完
      6. `other`     —— 都不命中

    旧项目书 23 实测分布：`mid_action 23 / other 21 / omen 2 / dialogue 1 /
    summary 0 / time_skip 0`。

    ★ 注意 `summary = 0` 与 `time_skip = 0` —— 说明旧项目
    「不要总结式结尾」的规则**确实生效了**。因此本函数**只分类，不判罚**；
    `02-根因与架构对策` 的「不要做」清单里明确写着
    「不要把总结式结尾当硬伤」。
    """
    raw = (text or "").strip()
    if not raw:
        return EndingPattern.OTHER

    last_para = _last_nonempty_paragraph(raw)
    lp_tail = last_para[-window:] if last_para else ""
    tail = raw[-window:]
    norm_tail1 = norm_whitespace(raw)[-1:] if raw else ""

    # 1. 时间流逝：末段末句/首句以时间状语开头，或末 60 字含时间流逝语
    time_hit = False
    sentences = [s for s in re.split(r"[。！？…\n]+", last_para) if s.strip()]
    if sentences:
        last_sent = sentences[-1].strip()
        if any(last_sent.startswith(w) for w in TIME_HEAD):
            time_hit = True
        if len(last_para) <= 60 and any(last_para.startswith(w) for w in TIME_HEAD):
            time_hit = True
    if any(w in tail for w in TIME_TAIL):
        time_hit = True

    # 2/3. 预兆 / 总结（末段尾部或末 60 字）
    omen_hit = any(w in lp_tail or w in tail for w in OPEN_TEASER_ENDINGS)
    summary_hit = any(w in lp_tail or w in tail for w in SUMMARY_ENDINGS)

    # 4. 引号收尾
    ends_quote = norm_tail1 in ("”", "」", "』", '"')

    # 5. 未写完 / 动作收束
    unfinished = norm_tail1 in ("…", "，", "、", "——", "：", "-")
    action_hit = False
    if sentences:
        win = sentences[-1].strip()[-10:]
        action_hit = any(w in win for w in ACTION_TAIL)

    if time_hit:
        return EndingPattern.TIME_SKIP
    if omen_hit:
        return EndingPattern.OMEN
    if summary_hit:
        return EndingPattern.SUMMARY
    if ends_quote:
        return EndingPattern.DIALOGUE
    if unfinished or action_hit:
        return EndingPattern.MID_ACTION
    return EndingPattern.OTHER


def find_cliches(text: str) -> list[str]:
    """套话命中（去重，保留首次出现顺序）。"""
    seen: list[str] = []
    for c in CLICHES:
        if c in (text or "") and c not in seen:
            seen.append(c)
    return seen


def count_strong_emotion_words(text: str) -> int:
    """强情绪词总出现次数。

    旧项目 writer 规则：「强情绪词每章 ≤2 次，其余用差异化行为呈现」。
    """
    return sum((text or "").count(w) for w in STRONG_EMOTION_WORDS)


def simile_count(text: str) -> int:
    """比喻（明喻）出现次数。

    用户痛点 3「比喻太多」的字面层抓手。
    ⚠️ 旧项目代码层零实现，只有 Editor 的语义判断（提到 43 次）。
    本指标是**触发器**，不是终判。
    """
    return sum(len(p.findall(text or "")) for p in _SIMILE_PATTERNS)


def sentence_length_cv(text: str) -> float | None:
    """句长变异系数（AI 文本句长过于均匀）。

    以 `。！？…` 分句。少于 2 句 → `None`。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    parts = [p for p in re.split(r"[。！？…]+", raw) if p.strip()]
    if len(parts) < 2:
        return None
    lens = [len(p) for p in parts]
    mean = sum(lens) / len(lens)
    if mean <= 0:
        return None
    var = sum((x - mean) ** 2 for x in lens) / len(lens)
    return (var**0.5) / mean


def top_repeated_ngrams(text: str, *, n: int = 4, k: int = 3) -> list[str]:
    """本章词频前 K 的 n-gram（并列取首次出现顺序）。

    ⚠️ `Counter.most_common` 的顺序语义是 baseline 的一部分：
    换成字典序会得到 9/10/4/4 而不是 11/7/6/6（`recurring_ngrams` docstring）。
    """
    gs = grams(norm_whitespace(text), n)
    if not gs:
        return []
    return [g for g, _ in Counter(gs).most_common(k)]


def starts_with_time_skip(text: str, *, window: int = 40) -> bool:
    """章首是否用时间/场景硬切词（违反「章首承接」）。

    旧项目 `bridge_notes` 用它判 `must_review`。
    """
    head = (text or "").strip()[:window]
    return any(w in head for w in BRIDGE_BREAK_WORDS)


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #


@dataclass
class LiteralMetrics:
    """一章的字面层指标。字段名与旧项目 `prose_style.analyze` 对齐。"""

    chars: int = 0
    chars_raw: int = 0
    dialogue_ratio: float | None = None
    intra_redundancy: float | None = None
    cross_chapter_overlap: float | None = None
    prev_chapters_used: int = 0
    triple_parallelism: int = 0
    ending: EndingPattern = EndingPattern.OTHER
    cliches: list[str] = field(default_factory=list)
    strong_emotion_words: int = 0
    simile_count: int = 0
    sentence_len_cv: float | None = None
    top_ngrams: list[str] = field(default_factory=list)
    starts_with_time_skip: bool = False
    ngram: int = 4

    def to_dict(self) -> dict[str, Any]:
        return {
            "chars": self.chars,
            "chars_raw": self.chars_raw,
            "dialogue_ratio": self.dialogue_ratio,
            "intra_redundancy": self.intra_redundancy,
            "cross_chapter_overlap": self.cross_chapter_overlap,
            "prev_chapters_used": self.prev_chapters_used,
            "triple_parallelism": self.triple_parallelism,
            "ending": self.ending.value,
            "cliches": list(self.cliches),
            "strong_emotion_words": self.strong_emotion_words,
            "simile_count": self.simile_count,
            "sentence_len_cv": self.sentence_len_cv,
            "top_ngrams": list(self.top_ngrams),
            "starts_with_time_skip": self.starts_with_time_skip,
            "ngram": self.ngram,
        }


def analyze(
    text: str,
    *,
    prev_texts: list[str] | None = None,
    n: int = 4,
    top_k: int = 3,
) -> LiteralMetrics:
    """单章字面层度量汇总（零 LLM、零 I/O、确定性）。

    参数：
      · `prev_texts` —— 前 N 章正文（**最近的在最后**）；
        未提供 → `cross_chapter_overlap=None`（缺省即不通过，**不是 0**）
      · `n` —— n-gram 长度（口径冻结，改则 baseline 失效）
    """
    raw = text or ""
    norm = norm_whitespace(raw)
    return LiteralMetrics(
        chars=len(norm),
        chars_raw=len(raw.strip()),
        dialogue_ratio=dialogue_ratio(raw),
        intra_redundancy=intra_redundancy(raw, n=n),
        cross_chapter_overlap=cross_chapter_overlap(raw, prev_texts or [], n=n),
        prev_chapters_used=len(prev_texts or []),
        triple_parallelism=triple_parallelism_count(raw),
        ending=classify_ending(raw),
        cliches=find_cliches(raw),
        strong_emotion_words=count_strong_emotion_words(raw),
        simile_count=simile_count(raw),
        sentence_len_cv=sentence_length_cv(raw),
        top_ngrams=top_repeated_ngrams(raw, n=n, k=top_k),
        starts_with_time_skip=starts_with_time_skip(raw),
        ngram=n,
    )


def recurring_ngrams(
    texts: list[str], *, n: int = 4, k: int = 3, min_chapters: int = 2
) -> list[dict]:
    """跨章复现的 n-gram：逐章取词频前 K，再统计出现在多少章。

    复现 baseline 口径 —— 书 23 实测：`了一下。`/`。”陈砚`/`第三十七`/`第三十八`
    分别出现在 **11/7/6/6** 章（`n=4, k=3`）。
    返回按章数降序、n-gram 升序（确定性）。
    """
    chapters: Counter = Counter()
    totals: Counter = Counter()
    for t in texts or []:
        gs = grams(norm_whitespace(t), n)
        if not gs:
            continue
        cnt = Counter(gs)
        for g, c in cnt.most_common(k):
            chapters[g] += 1
            totals[g] += c
    rows = [
        {"ngram": g, "chapters": c, "total": totals[g]}
        for g, c in chapters.items()
        if c >= max(1, min_chapters)
    ]
    rows.sort(key=lambda r: (-r["chapters"], r["ngram"]))
    return rows


__all__ = [
    "CLICHES",
    "SUMMARY_ENDINGS",
    "AI_SCENE_ENDINGS",
    "OPEN_TEASER_ENDINGS",
    "BRIDGE_BREAK_WORDS",
    "STRONG_EMOTION_WORDS",
    "LiteralMetrics",
    "norm_whitespace",
    "grams",
    "intra_redundancy",
    "cross_chapter_overlap",
    "dialogue_ratio",
    "triple_parallelism_count",
    "find_cliches",
    "count_strong_emotion_words",
    "simile_count",
    "sentence_length_cv",
    "top_repeated_ngrams",
    "classify_ending",
    "starts_with_time_skip",
    "analyze",
    "recurring_ngrams",
]
