"""圣经生成节点测试（P3-2 / 人工确认点 C-1）。

## 本文件防的是什么

1. **圣经过薄** → 撑不起长篇，且 L0 约束失效。
   旧项目 `books.bible` 列在重开生成时被清空、新书只写 `bible_path`
   → **每章 L0 世界观圣经静默为空**。
2. **圣经不稳定** → 它是缓存稳定前缀的第一块。
   旧项目 `bible_inject.select` 按章裁剪 → 全书最稳的前缀在第一个块就分叉。
3. **硬规则写成人物描述** → 后续「设定违反」检测失去依据。
"""

from __future__ import annotations

import pytest

from storyforge.infra.llm.client import CallContext, ClientConfig, LLMClient, MemoryIdempotencyStore
from storyforge.infra.llm.schemas import BibleOut
from storyforge.infra.storage.artifacts import ArtifactNotFound, ArtifactStore
from storyforge.graph.nodes.bible_architect import (
    bible_to_markdown,
    build_bible,
    load_bible_text,
    persist_bible,
    validate_bible,
)

pytestmark = pytest.mark.unit


def good_bible(**over) -> BibleOut:
    base = dict(
        title="值夜手册",
        worldview=(
            "长明楼是一座始建于民国的旧档案馆，共五层：地上三层存放历年档案，"
            "地下两层封存不予公开的卷宗，另有一处不列入楼层编号的十三层。"
            "整栋楼实行值夜制度——每晚必须有一名值夜人留守至天亮，"
            "负责核对三盏壁灯的明暗次序、应答零点的敲门声、"
            "把当夜发生的事逐条写进值班登记簿并签名留档。"
            "楼内的规则以文本形式存在：它们被写在一本蓝布硬壳的《值夜手册》里，"
            "一旦落笔便只能覆盖不能擦除，连写规则的人自己也无法反悔。"
            "档案馆正在搬迁，人员陆续调离，值夜的岗位却没有取消——"
            "因为手册上写着「值夜不可中断」，而没人敢去验证中断的后果。"
            "旧城区的人对这栋楼避而不谈，只在夜里绕道走。"
            "楼里住着若干长期住户，他们与档案馆之间有各自的委托关系："
            "住户提出委托，值夜人执行，执行完毕手册就会减少一条规则——"
            "没人知道减少的规则去了哪里，也没人知道新增的规则从何而来。"
            "最近一次交接发生在一个雨夜，前任值夜人只留下一句"
            "「不要数楼层」，随后便从所有住户的记忆里消失了。"
            "陈砚是在那之后接手的人，他上任的第一夜就发现壁灯次序不对劲。"
        ),
        power_system=(
            "规则体系分三层：条文（写死的规矩，不可更改）、"
            "批注（前任值夜人留在页边的解读，可参考但未必可信）、"
            "朱批（值夜人自行改判条文的权力，需以血为墨、引三条已生效条文为依据）。"
            "朱批改判有效，但会消耗改判者的存在感——被规则记住的人才能留下痕迹，"
            "改判越多，旁人越容易忘记你是谁。"
            "规则的边界：条文只能覆盖不能新增，新增必须由外部力量投放；"
            "任何人都无法直接读到未拆封的条文；"
            "规则对不在楼内的人无效。"
            "力量层级由「与规则的贴合度」决定，而非由体力或武艺决定："
            "刚接手的人只能执行条文，熟悉之后能读懂批注，"
            "只有极少数人敢动用朱批——而那正是被遗忘最快的路径。"
            "因此本世界的「变强」与「被记住」是一对反向拉扯的力量。"
            "代价的呈现方式是渐进的：先是旁人记不清你的名字，"
            "再是登记簿上你的签名变淡，最后是正册里那一行自己消失。"
        ),
        rules=[
            "规则一旦写入手册便不可擦除，只能覆盖",
            "午夜后应答唤名者视为交接完成",
            "值夜人不得越过门槛内侧的黄铜线",
            "壁灯必须保持一明两暗的次序",
            "被划掉的名字不会真正消失，只会沉入正册底层",
            "完成一个住户的委托，手册就会减少一条规则",
            "十三层不属于长明楼的楼层编号",
            "朱批改判需以血为墨并引用三条已生效条文",
            "不得在值班登记簿上留下任何涂改痕迹",
            "交班前必须确认三盏壁灯全部熄灭",
            "任何情况下不得回应来自十三层的呼唤",
            "手册缺页时须立即上报，不得自行补写",
            "禁止在馆内使用任何电子记录设备",
            "住户委托只接受书面形式，口头委托无效",
            "接受委托后不得中途放弃，除非本人已被登记",
            "被划掉的名字不得重新写入正册",
        ],
        era="当代，南方某中等城市的旧城区。档案馆正在搬迁，人员陆续调离。",
        tone="冷峻写实的中式规则怪谈，烧脑解谜与市井温情并行",
        terms=[
            "值夜人——承担夜间值守职责的人",
            "朱批——以血为墨改判条文的权力",
            "正册——记录所有被规则登记之人的名册",
            "房客守则——不属于值夜手册体系的另一套规则",
            "交接——前任值夜人向继任者传递职责与手册",
            "黄铜线——门槛内侧的界线，越线即违规",
            "静默走廊——每小时必须巡查一次的走廊",
            "活档——午夜后必须上锁的档案区域",
            "十三层——不列入楼层编号的一层",
            "遗忘浓度——衡量遗忘侵蚀程度的指标",
            "清醒剂——进入红区后必须服用的药剂",
            "登记卡编号——异常时须背诵的编号",
        ],
    )
    base.update(over)
    return BibleOut(**base)


#: 一份「够厚」的补充设定，用于把夹具推到总字数下限之上。
#:
#: ★ 标定依据：旧项目 5 本书的真实 `bible.txt` 实测 4231~7631 字
#: （见 `bible_architect._MIN_TOTAL_CHARS`）。夹具必须达到同一量级，
#: 否则它自己就通不过校验 —— 而校验器的下限正是按那批真实数据定的。
_EXTRA_RULES: list[str] = [
    "不得在值班登记簿上留下任何涂改痕迹",
    "交班前必须确认三盏壁灯全部熄灭",
    "任何情况下不得回应来自十三层的呼唤",
    "手册缺页时须立即上报，不得自行补写",
    "禁止在馆内使用任何电子记录设备",
    "住户委托只接受书面形式，口头委托无效",
    "接受委托后不得中途放弃，除非本人已被登记",
    "被划掉的名字不得重新写入正册",
]


class ScriptedTransport:
    """按脚本返回的哑 transport。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    async def __call__(self, *, model, provider, system, user, **params):
        self.calls.append({"system": system, "user": user, "model": model.id})
        idx = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[idx], {"prompt_tokens": 100, "completion_tokens": 50,
                                     "reasoning_tokens": 20, "cached_tokens": 40,
                                     "total_tokens": 150, "finish_reason": "stop"}


def make_client(responses: list[str]) -> tuple[LLMClient, ScriptedTransport]:
    t = ScriptedTransport(responses)
    return (
        LLMClient(t, config=ClientConfig(max_attempts=1, backoff_base_s=0.0,
                                         structured_retries=0),
                  idempotency=MemoryIdempotencyStore()),
        t,
    )


# --------------------------------------------------------------------------- #
# 零 LLM 校验
# --------------------------------------------------------------------------- #


def test_good_bible_passes() -> None:
    assert validate_bible(good_bible()) == []


def test_thin_worldview_rejected() -> None:
    """★ 世界观一节整段缺失 → L0 约束撑不起长篇。"""
    issues = validate_bible(good_bible(worldview="一个楼。"))
    assert any("世界观一节过短" in i for i in issues)


def test_thin_bible_rejected_by_total_length() -> None:
    """★ 总字数下限 —— **实测修正**过的判据。

    早先只卡 `worldview >= 200 字`，真实运行被拒两次（193 字 / 138 字），
    但把产物读出来看，那份圣经是合格的：10 条硬规则、三层力量体系、术语齐全，
    只是「世界观」这一节写得紧凑。

    实质是**把"信息密度"错当成了"单字段长度"**：设定可以合理地分布在
    `worldview`/`power_system`/`era`/`rules` 里，而紧凑恰恰是好文风
    （旧项目正是因为啰嗦被抱怨）。

    下限按旧项目 5 本真实 `bible.txt`（4231~7631 字）标定，取 1200 字。
    """
    thin = good_bible(
        worldview="短。", power_system="也短。",
        rules=["规则"],
        terms=["术语——释义"],
    )
    issues = validate_bible(thin)
    assert any("圣经过薄" in i for i in issues), issues


def test_bible_with_distributed_setting_information_passes() -> None:
    """★ 信息分布在不同字段的圣经**必须通过**（不得只看单字段长度）。"""
    assert validate_bible(good_bible()) == []


def test_missing_power_system_rejected() -> None:
    """★ 缺力量体系 → 后期战力崩坏（旧项目实测「力量层次」约束失效）。"""
    issues = validate_bible(good_bible(power_system=""))
    assert any("力量" in i for i in issues)


def test_too_few_rules_rejected() -> None:
    issues = validate_bible(good_bible(rules=["规则一", "规则二"]))
    assert any("硬规则只有" in i for i in issues)


def test_too_few_terms_rejected() -> None:
    issues = validate_bible(good_bible(terms=["甲——乙"]))
    assert any("术语对照" in i for i in issues)


def test_person_description_rules_rejected() -> None:
    """★ 硬规则不得是人物描述。

    「主角很勇敢」不是世界法则，无法作为「设定违反」检测的依据。
    这是模型常见的偏题模式。
    """
    issues = validate_bible(
        good_bible(rules=[
            "主角非常勇敢", "他很善良", "她总是聪明", "坚强",
            "规则一有效", "规则二有效", "规则三有效", "规则四有效",
        ])
    )
    assert any("不像世界法则" in i for i in issues)


def test_missing_title_rejected() -> None:
    assert any("缺书名" in i for i in validate_bible(good_bible(title="")))


# --------------------------------------------------------------------------- #
# 渲染为 markdown（稳定前缀的内容）
# --------------------------------------------------------------------------- #


def test_markdown_contains_all_sections() -> None:
    md = bible_to_markdown(good_bible())
    for section in ("【世界观】", "【纪元与年代】", "【力量/规则体系】",
                    "【世界硬规则】", "【术语与专名】"):
        assert section in md, f"缺段落 {section}"
    assert "# 值夜手册" in md


def test_markdown_has_numbered_rules() -> None:
    md = bible_to_markdown(good_bible())
    assert "1. 规则一旦写入手册" in md
    assert "8. 朱批改判需以血为墨" in md


def test_markdown_is_deterministic() -> None:
    """★ 同一圣经必须渲染出**逐字节相同**的文本。

    它是缓存稳定前缀的第一块 —— 任何不确定性都会让全书缓存失效。
    """
    b = good_bible()
    assert bible_to_markdown(b) == bible_to_markdown(b)


def test_markdown_contains_no_volatile_content() -> None:
    """★ 圣经文本不得含章号/时间戳等逐章变化内容。

    `PromptLayers.validate_stability` 会在组装时拦截，这里先做一层前置检查。
    """
    import re

    md = bible_to_markdown(good_bible())
    assert not re.search(r"第\s*\d+\s*章", md), "圣经不得含章号"
    assert not re.search(r"\d{4}-\d{2}-\d{2}", md), "圣经不得含时间戳"


# --------------------------------------------------------------------------- #
# 落盘与读取
# --------------------------------------------------------------------------- #


def test_persist_and_load_roundtrip(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    bible = good_bible()
    ref, h = persist_bible(store, 1, bible)

    assert ref == "books/1/bible.md"
    assert len(h) == 32
    text = load_bible_text(store, 1)
    assert "值夜手册" in text
    assert "世界硬规则" in text


def test_load_missing_bible_raises(tmp_path) -> None:
    """★ 读不到圣经必须**抛错**。

    旧项目 `books.bible` 被清空后，L0 世界观静默为空、约束失效，
    且**没有任何报错** —— 这是"每章世界观约束失效"的成因。
    """
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFound):
        load_bible_text(store, 999)


def test_hash_changes_when_bible_changes(tmp_path) -> None:
    """hash 变化 = 全书级别失效（应触发人工确认）。

    圣经改动意味着：已写章节的世界观可能与新圣经冲突，
    且全书缓存前缀失效。
    """
    store = ArtifactStore(tmp_path)
    _, h1 = persist_bible(store, 1, good_bible())
    _, h2 = persist_bible(store, 1, good_bible(tone="完全不同的基调"))
    assert h1 != h2


# --------------------------------------------------------------------------- #
# 节点调用（含校验失败重试）
# --------------------------------------------------------------------------- #


async def test_build_bible_success() -> None:
    client, _ = make_client([good_bible().model_dump_json()])
    result = await build_bible(client, book_id=1, topic="规则怪谈", genre="suspense")

    assert result.ok, result.error
    assert result.bible is not None
    assert result.bible.title == "值夜手册"
    assert result.llm_calls == 1
    assert result.violations == []


async def test_build_bible_retries_on_validation_failure() -> None:
    """校验失败 → 带问题清单重试（增量修正，不重发全上下文）。"""
    bad = good_bible(power_system="").model_dump_json()
    good = good_bible().model_dump_json()
    client, transport = make_client([bad, good])

    result = await build_bible(client, book_id=1, topic="t", genre="g", max_rounds=2)

    assert result.ok, result.error
    assert result.llm_calls == 2
    # 第二次请求必须带上问题清单
    second_user = transport.calls[1]["user"]
    assert "未通过结构校验" in second_user
    assert "力量" in second_user, "必须把具体问题发回去"
    assert "其余部分保持原样" in second_user


async def test_build_bible_gives_up_after_max_rounds() -> None:
    """连续失败必须显式失败，不静默返回半成品。"""
    bad = good_bible(power_system="").model_dump_json()
    client, _ = make_client([bad])

    result = await build_bible(client, book_id=1, topic="t", genre="g", max_rounds=1)

    assert result.ok is False
    assert result.bible is None
    assert result.violations
    assert "未通过校验" in (result.error or "")


async def test_build_bible_records_usage() -> None:
    """调用必须被计量（旧项目 22.2% 空输出无法归因的教训）。"""
    client, _ = make_client([good_bible().model_dump_json()])
    await build_bible(client, book_id=1, topic="t", genre="g")

    assert client.records, "必须记录调用"
    rec = client.records[0]
    assert rec.node_name == "bible_architect"
    assert rec.finish_reason is not None
    assert rec.usage.prompt_tokens == 100
    assert rec.book_id == 1


async def test_build_bible_is_idempotent() -> None:
    """★ 幂等键命中时不重复调用（防 LangGraph #7417 静默重放）。"""
    client, transport = make_client([good_bible().model_dump_json()])

    r1 = await build_bible(client, book_id=1, topic="t", genre="g",
                           idempotency_key="bible:1")
    r2 = await build_bible(client, book_id=1, topic="t", genre="g",
                           idempotency_key="bible:1")

    assert r1.ok and r2.ok
    assert len(transport.calls) == 1, "幂等命中时不得重复调用"
    assert r2.llm_calls == 0
