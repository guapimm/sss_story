"""单章闭环测试（P4）—— ★ 本文件是「审校说了话没人听」的回归防线。

## 核心验收

旧项目最贵的单点缺陷：

    `must_review` 全仓 **25 个命中、0 个读取点**（唯一近似读取是写进 trace metadata）
    → Editor 返回 `revised_content: null` 则**正文一字不改入库**
    → 生产实测 **185/210 章（88.1%）**被点名文风问题，其中 **57 章仍判 passed**

本文件验证本实现把这条链路**闭环**：
  · `issues` 非空 ⇒ `revised_content` 必填（schema 层强制，不靠 prompt 请求）
  · 修订稿必须**过闸门**（分数不升则拒绝采用并留痕）
  · 拒答/空输出必须**显式失败**，不得作为正文入库
  · 就绪闸门必须**干净退出**（not_ready 是调度态，不是失败）
  · C-7 必须能止损（重写达上限 → 停下等人工，不无限烧 token）
"""

from __future__ import annotations

import pytest

from storyforge.domain.enums import BeatType, ConfirmPoint
from storyforge.domain.outline import ChapterPlan, SceneBeat, WorldStateSnapshot
from storyforge.graph.graphs.chapter import (
    BLOCKED,
    NOT_READY,
    READY,
    ChapterDeps,
    build_chapter_graph,
    needs_c7_confirm,
)
from storyforge.graph.graphs.chapter_nodes import check_readiness, should_review
from storyforge.graph.nodes.chapter_writer import (
    assemble_context,
    count_chars,
    detect_refusal,
    load_prose,
    persist_prose,
    render_health_beats,
    render_plot_detail,
    render_state,
    write_chapter,
)
from storyforge.graph.nodes.detail_planner import persist_plan
from storyforge.graph.nodes.extractors import (
    ENTITY_RESOLVER_SYSTEM,
    RELATION_EXTRACTOR_SYSTEM,
    SCENE_EXTRACTOR_SYSTEM,
    render_known_entities,
    validate_relations,
    validate_scene,
)
from storyforge.graph.nodes.reviewer import (
    CATEGORY_RULES,
    apply_revision_if_better,
    build_review_user,
    build_targeted_revision_prompt,
)
from storyforge.infra.llm.client import (
    CallContext,
    ClientConfig,
    LLMClient,
    MemoryIdempotencyStore,
)
from storyforge.infra.llm.schemas import (
    ChapterPlanOut,
    EntityMentionOut,
    EventOut,
    InjuryUpdateOut,
    RelationExtractionOut,
    RelationUpdateOut,
    ReviewIssueOut,
    ReviewResultOut,
    SceneBeatOut,
    SceneExtractionOut,
)
from storyforge.infra.storage.artifacts import ArtifactNotFound, ArtifactStore
from storyforge.scorecard.aggregate import evaluate

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

#: 一段"像正文"的长文本（供长度闭环与打分使用）
GOOD_PROSE = (
    "陈砚把手电筒夹在腋下，腾出手去够那把铜钥匙。锁孔里塞着东西。\n\n"
    "「别开。」周德海在楼梯口说。\n\n"
    "陈砚没回头。他知道老周为什么拦他——上一次开这扇门的人，"
    "第二天就从名册上消失了，连登记簿上的签名都褪成了空白。\n\n"
    "钥匙转到底，门缝里透出一线冷光。里面不是档案室。\n\n"
    "是一段向下延伸的楼梯，扶手上落着灰，灰上没有脚印。\n\n"
    "「十三层。」周德海的声音很近，近得不像他平时那样隔着门说话，"
    "「你母亲当年也是站在这儿。」\n\n"
    "陈砚把钥匙拔了出来。他没有下去。他转过身，看着老周的眼睛，"
    "一个字一个字地说：「你刚才说，你从没上过三楼。」\n\n"
    "周德海脸上的表情没有变。但他往后退了半步。\n"
)


def plan(global_no: int = 1, **over) -> ChapterPlan:
    base = dict(
        global_no=global_no,
        volume_no=1,
        title="残页上的第十三条",
        plot="陈砚打开档案室的门，发现通往十三层的楼梯，并当面质问周德海",
        scenes=[
            SceneBeat(seq=i, goal=f"目标{i}", obstacle=f"阻碍{i}",
                      outcome=f"结果{i}", why_now=f"动机{i}")
            for i in range(4)
        ],
        entry_state=WorldStateSnapshot(
            location_name="长明楼三层", story_time="第三日夜",
            present_names=["陈砚"], open_thread="残页缺一页",
        ),
        exit_state=WorldStateSnapshot(
            location_name="长明楼三层", story_time="第三日夜",
            present_names=["陈砚", "周德海"], open_thread="周德海说了谎",
        ),
        end_hook="周德海往后退了半步。",
    )
    base.update(over)
    return ChapterPlan(**base)


class ScriptedTransport:
    """按**节点签名**分派（与调用顺序无关）。"""

    SIGNATURES: tuple[tuple[str, str], ...] = (
        ("chapter_writer", "资深网文小说作家"),
        ("targeted_reviser", "资深网文小说作家"),
        ("style_critic", "资深网文主编"),
        ("scene_extractor", "小说记录员"),
        ("entity_resolver", "实体归一器"),
        ("relation_extractor", "社会关系抽取器"),
    )

    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def _node_of(self, system: str) -> str:
        for node, sig in self.SIGNATURES:
            if sig in system:
                return node
        raise AssertionError(f"无法识别节点：{system[:60]!r}")

    async def __call__(self, *, model, provider, system, user, **params):
        node = self._node_of(system)
        self.calls.append({"node": node, "system": system, "user": user})
        if node not in self.responses:
            raise AssertionError(f"没有为节点 {node!r} 准备脚本响应")
        return self.responses[node], {
            "prompt_tokens": 100, "completion_tokens": 50,
            "reasoning_tokens": 20, "cached_tokens": 40,
            "total_tokens": 150, "finish_reason": "stop",
        }


def make_client(responses: dict[str, str]) -> tuple[LLMClient, ScriptedTransport]:
    t = ScriptedTransport(responses)
    return (
        LLMClient(t, config=ClientConfig(max_attempts=1, backoff_base_s=0.0,
                                         structured_retries=0),
                  idempotency=MemoryIdempotencyStore()),
        t,
    )


# --------------------------------------------------------------------------- #
# P4-1 上下文组装
# --------------------------------------------------------------------------- #


def test_plot_detail_renders_all_four_parts() -> None:
    """细纲拍点表必须是「目标/阻碍/结果/为何是现在」四项齐全。"""
    text = render_plot_detail(plan())
    assert "目标0" in text and "阻碍0" in text
    assert "结果0" in text and "动机0" in text
    assert text.count("为何是现在") == 4


def test_context_carries_both_anchors() -> None:
    """★ 前后锚点必须都给。

    旧项目实测章间衔接断裂是最严重缺陷，根因**不是细纲长度**，
    而是 writer 只知道自己这一章。
    """
    ctx = assemble_context(
        plan(2),
        prev_exit=WorldStateSnapshot(location_name="长明楼三层", story_time="第三日夜",
                                     open_thread="陈砚拒绝开门"),
        next_plan=plan(3),
    )
    assert "长明楼三层" in ctx.prev_exit_state
    assert "陈砚拒绝开门" in ctx.prev_exit_state
    assert ctx.next_entry_state, "下一章入口状态必须给出"
    assert ctx.next_title


def test_context_includes_health_beats() -> None:
    """★ 伤势节拍必须进写作上下文（这是恢复率 0% 的最后一环）。"""
    from storyforge.domain.enums import HealthAction
    from storyforge.domain.health import HealthBeat

    p = plan()
    p.health_beats = [
        HealthBeat(injury_id=7, action=HealthAction.HEALED, chapter_no=1,
                   scene_seq=2, note="左肩剑创痊愈")
    ]
    ctx = assemble_context(p)
    assert "左肩剑创痊愈" in ctx.health_beats
    assert "第3个场景" in ctx.health_beats, "必须指明落在哪个场景"


def test_render_state_includes_known_facts() -> None:
    """fog-of-war：谁知道什么必须能被渲染（旧项目完全没有此能力）。"""
    s = WorldStateSnapshot(
        location_name="长明楼", story_time="夜",
        known_facts={1: ["手册缺一页"]},
    )
    text = render_state(s)
    assert "长明楼" in text and "手册缺一页" in text


def test_render_state_of_none_is_empty() -> None:
    assert render_state(None) == ""


# --------------------------------------------------------------------------- #
# P4-3 写作：拒答检测与长度闭环
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("text", [
    "抱歉，我无法完成这个请求。",
    "I'm sorry, but I can't write that.",
    "作为一个AI，我没有个人经历。",
    "请提供更多细节，我才能继续。",
    "以下是为你撰写的小说正文：",
    "```\n正文\n```",
])
def test_refusal_detected(text: str) -> None:
    """★ "非空但不是正文"的输出必须被拦住。

    它比空输出更危险：空输出会被 `CallStatus.BLANK` 抓到，
    而"道歉信"是**非空**的，会作为正文一路入库。
    """
    assert detect_refusal(text) is not None


def test_normal_prose_not_flagged_as_refusal() -> None:
    assert detect_refusal(GOOD_PROSE) is None


def test_count_chars_ignores_whitespace() -> None:
    assert count_chars("陈砚 推开\n门") == 5


async def test_write_chapter_success() -> None:
    client, _ = make_client({"chapter_writer": GOOD_PROSE})
    result = await write_chapter(
        client, book_id=1, plan=plan(), bible_text="【世界观】x",
        volume_block_text="【本卷】第1卷",
        min_chars=10, soft_max=9000,   # 关掉长度闭环，本用例只验直通
    )
    assert result.ok, result.error
    assert result.prose.strip() == GOOD_PROSE.strip()
    assert result.chars == count_chars(GOOD_PROSE)
    assert result.llm_calls == 1
    assert result.continued is False


async def test_write_chapter_rejects_refusal() -> None:
    """★ 拒答必须显式失败，**不得**作为正文入库。"""
    client, _ = make_client({"chapter_writer": "抱歉，我无法完成这个请求。"})
    result = await write_chapter(
        client, book_id=1, plan=plan(), bible_text="x",
        volume_block_text="v",
    )
    assert result.ok is False
    assert result.prose == "", "拒答文本不得被当成正文"
    assert any("拒答" in p for p in result.problems)


async def test_write_chapter_continues_when_short() -> None:
    """★ 长度闭环：不足则**续写**，不是重写整章。

    重写一次的成本等于再写一章；续写只发尾部指令。
    """
    short = "陈砚推开门。"
    long_tail = "里面是楼梯。" * 60
    client, transport = make_client({
        "chapter_writer": short,
        # 第二次调用是续写 —— transport 按节点分派，所以同一个 key
        # 需要按调用序号返回不同内容
    })
    # 手工按调用次数返回不同内容
    calls: list[str] = []

    async def seq_transport(*, model, provider, system, user, **params):
        calls.append(user)
        return (short if len(calls) == 1 else long_tail), {
            "prompt_tokens": 10, "completion_tokens": 10,
            "total_tokens": 20, "finish_reason": "stop",
        }

    client2 = LLMClient(seq_transport, config=ClientConfig(
        max_attempts=1, backoff_base_s=0.0), idempotency=MemoryIdempotencyStore())
    result = await write_chapter(
        client2, book_id=1, plan=plan(), bible_text="x", volume_block_text="v",
        min_chars=100, soft_max=9000, max_continue=1,
    )
    assert result.ok, result.error
    assert result.continued is True
    assert result.chars >= 100
    assert len(calls) == 2
    assert "续写指令" in calls[1], "第二次调用必须是续写指令"
    assert "已写正文" in calls[1]
    del client, transport


async def test_write_chapter_records_over_length_without_truncating() -> None:
    """★ 超长只记录、**不截断**。

    截断会切断句子并丢掉章末钩子 —— 钩子是下一章的入口，比超长更伤。
    """
    long_prose = "陈砚推开门。" * 400
    client, _ = make_client({"chapter_writer": long_prose})
    result = await write_chapter(
        client, book_id=1, plan=plan(), bible_text="x", volume_block_text="v",
        min_chars=10, soft_max=50, max_continue=0,
    )
    assert result.ok
    assert result.prose == long_prose, "不得截断"
    assert any("超上限" in p for p in result.problems)


async def test_write_chapter_is_idempotent() -> None:
    """★ 防 `langgraph#7417`：>180s 的调用被静默重放 → 重复计费。

    正文生成正是最容易超 180s 的调用。
    """
    client, transport = make_client({"chapter_writer": GOOD_PROSE})
    kw = dict(book_id=1, plan=plan(), bible_text="x", volume_block_text="v",
              min_chars=10, soft_max=9000, idempotency_key="chapter:write:1:1")
    r1 = await write_chapter(client, **kw)
    r2 = await write_chapter(client, **kw)
    assert r1.ok and r2.ok
    assert len(transport.calls) == 1, "幂等命中不得重复调用"
    assert r2.llm_calls == 0


async def test_continue_uses_distinct_idempotency_key() -> None:
    """★ 续写必须用**不同的**幂等键。

    否则续写会命中"首次写作"的缓存，把同一段正文当成续写内容拼回去
    —— 实测踩到：正文被重复一遍，长度看着"达标"了，内容却是坏的。
    """
    short = "陈砚推开门。"
    calls: list[str] = []

    async def seq_transport(*, model, provider, system, user, **params):
        calls.append(user)
        return (short if len(calls) == 1 else "里面是楼梯。" * 60), {
            "prompt_tokens": 10, "completion_tokens": 10,
            "total_tokens": 20, "finish_reason": "stop",
        }

    client = LLMClient(seq_transport, config=ClientConfig(
        max_attempts=1, backoff_base_s=0.0), idempotency=MemoryIdempotencyStore())
    result = await write_chapter(
        client, book_id=1, plan=plan(), bible_text="x", volume_block_text="v",
        min_chars=100, soft_max=9000, max_continue=1,
        idempotency_key="chapter:write:1:1",
    )
    assert result.ok, result.error
    assert result.continued is True
    assert len(calls) == 2, "幂等键冲突会让续写命中缓存而不真正调用"
    assert result.prose.count(short) == 1, "首次内容不得被重复拼接"


async def test_write_chapter_rejects_restart_instead_of_duplicating() -> None:
    """★ 续写若其实是"从头重写"，必须**丢弃**而不是拼接。

    这是最隐蔽的一类坏输出：拼接后字数"达标"、拒答检测通过、
    打分卡只看到"篇幅够" —— 但正文把同一段内容写了两遍。
    宁可留下"长度不足"这个**可见**缺陷，也不要"正文重复"这个**不可见**缺陷。
    """
    calls: list[str] = []

    async def restart_transport(*, model, provider, system, user, **params):
        calls.append(user)
        # 第一次正常写；第二次（续写）却从头发了一遍
        return (GOOD_PROSE if len(calls) == 1 else GOOD_PROSE + "结尾。"), {
            "prompt_tokens": 10, "completion_tokens": 10,
            "total_tokens": 20, "finish_reason": "stop",
        }

    client = LLMClient(restart_transport, config=ClientConfig(
        max_attempts=1, backoff_base_s=0.0), idempotency=MemoryIdempotencyStore())
    result = await write_chapter(
        client, book_id=1, plan=plan(), bible_text="x", volume_block_text="v",
        min_chars=100000, soft_max=200000, max_continue=1,
    )
    assert result.ok
    assert result.continued is False
    assert result.prose.strip() == GOOD_PROSE.strip(), "不得拼接重复内容"
    assert any("重新开头" in p for p in result.problems)


def test_looks_like_restart_detects_overlap() -> None:
    from storyforge.graph.nodes.chapter_writer import looks_like_restart

    body = GOOD_PROSE
    assert looks_like_restart(body, body[:60]) is True
    assert looks_like_restart(body, "楼梯下面传来脚步声，陈砚握紧了钥匙。") is False


def test_persist_and_load_prose(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ref = persist_prose(store, 3, 42, GOOD_PROSE)
    assert ref == "books/3/prose/00042.txt"
    assert load_prose(store, 3, 42) == GOOD_PROSE


def test_missing_prose_raises(tmp_path) -> None:
    """★ 读不到正文必须**抛错**。旧项目 `read_chapter` 静默返回空串。"""
    store = ArtifactStore(tmp_path)
    with pytest.raises(ArtifactNotFound):
        load_prose(store, 9, 999)


# --------------------------------------------------------------------------- #
# P4-2 就绪闸门
# --------------------------------------------------------------------------- #


def test_readiness_ready() -> None:
    status, reason = check_readiness(
        plan=plan(), plan_ref="r", global_no=5, detail_ready_through=10, lead_limit=3
    )
    assert status == READY and reason == ""


def test_readiness_not_ready_when_detail_behind() -> None:
    """★ 未就绪是**正常的调度态**，不是失败 —— 干净退出，理由明确。"""
    status, reason = check_readiness(
        plan=None, plan_ref="", global_no=10, detail_ready_through=3, lead_limit=3
    )
    assert status == NOT_READY
    assert "尚未就绪" in reason


def test_readiness_not_ready_when_lead_exceeded() -> None:
    """★ 正文最多领先细纲 3 章（决策 D-13）。

    防的是旧项目"正文跑太快 → 后续细纲全废"。
    """
    status, reason = check_readiness(
        plan=plan(20), plan_ref="r", global_no=20,
        detail_ready_through=10, lead_limit=3,
    )
    assert status == NOT_READY
    assert "领先细纲" in reason


def test_readiness_blocked_when_detail_should_exist() -> None:
    """★ 细纲**该有却没有** → `blocked`（真问题，必须与 not_ready 区分）。

    旧项目预生成线程在细纲未就绪时空转轮询，既不报错也不留痕。
    """
    status, reason = check_readiness(
        plan=None, plan_ref="", global_no=5, detail_ready_through=10, lead_limit=3
    )
    assert status == BLOCKED
    assert "找不到细纲产物" in reason


# --------------------------------------------------------------------------- #
# P4-6 审校路由
# --------------------------------------------------------------------------- #


def test_should_review_forced_for_golden_three() -> None:
    """★ 黄金三章无条件送审（D-14 C-5）。"""
    card = evaluate(GOOD_PROSE, global_no=1)
    needs, reason = should_review(card)
    assert needs is True
    assert "无条件" in reason


def test_should_review_for_volume_end() -> None:
    card = evaluate(GOOD_PROSE, global_no=50, is_volume_end=True)
    needs, reason = should_review(card, is_volume_end=True)
    assert needs is True


def test_should_not_review_good_chapter() -> None:
    """达标章不送审 —— 旧项目 standard 档 100 章里 88 章完全不审，本设计每章都有分。"""
    card = evaluate(GOOD_PROSE, global_no=50)
    if not card.needs_review:
        needs, reason = should_review(card)
        assert needs is False
        assert "通过" in reason


# --------------------------------------------------------------------------- #
# P4-7 审校：修订必须过闸门
# --------------------------------------------------------------------------- #


def test_review_schema_forbids_issues_without_revision() -> None:
    """★ `issues` 非空 ⇒ `revised_content` 必填（schema 层强制）。

    旧项目模型曾在 `notes` 里声称"已改写"而 `revised_content=null`。
    """
    import pydantic

    with pytest.raises(pydantic.ValidationError, match="revised_content"):
        ReviewResultOut(
            issues=[ReviewIssueOut(severity="major", category="ai_style",
                                   description="套话太多")],
            revised_content=None,
            score=6.0,
        )


def test_review_no_issues_needs_no_revision() -> None:
    ok = ReviewResultOut(issues=[], revised_content=None, score=9.0)
    assert ok.has_usable_revision is False


def test_has_usable_revision_ignores_whitespace() -> None:
    """★ **不采信模型自述**：只有实质修订稿才算修订。"""
    r = ReviewResultOut(issues=[], revised_content="   \n  ", score=8.0)
    assert r.has_usable_revision is False


def test_apply_revision_rejects_non_improving_draft() -> None:
    """★ 修订必须**过闸门**才算数（旧项目 `no_improve:6->6` 的防线）。

    分数不升 → 拒绝采用修订、保留原稿、并**留痕说明原因**。
    """
    outcome = apply_revision_if_better(
        GOOD_PROSE, "短。", global_no=50, plan=plan(50)
    )
    assert outcome.revision_applied is False
    assert outcome.final_prose == GOOD_PROSE
    assert "拒绝修订稿" in outcome.decision_note
    assert outcome.problems


def test_apply_revision_accepts_improving_draft() -> None:
    """分数上升 → 采用修订。"""
    poor = "短。" * 5
    outcome = apply_revision_if_better(poor, GOOD_PROSE, global_no=50, plan=plan(50))
    assert outcome.revision_applied is True
    assert outcome.final_prose == GOOD_PROSE
    assert outcome.score_after > outcome.score_before


def test_apply_revision_rejects_empty_draft() -> None:
    """空修订稿 → 拒绝（旧项目 `no_revised_content` 的形态）。"""
    outcome = apply_revision_if_better(GOOD_PROSE, "   ", global_no=50, plan=plan(50))
    assert outcome.revision_applied is False
    assert "为空" in outcome.decision_note


def test_targeted_revision_prompt_only_names_hit_categories() -> None:
    """★ 定向重写只强调**命中的缺陷类别**，不是"请写得更好"。

    旧项目 `no_improve:6->6` 说明泛泛要求会空转。
    """
    review = ReviewResultOut(
        issues=[
            ReviewIssueOut(severity="major", category="ai_style",
                           description="套话密集", location="首段"),
            ReviewIssueOut(severity="minor", category="pacing",
                           description="第二段注水", location="第二段"),
        ],
        revised_content="（略）",
        score=6.0,
    )
    prompt = build_targeted_revision_prompt(GOOD_PROSE, review)
    assert "AI 味" in prompt, "必须给出 ai_style 的具体规则"
    assert "节奏" in prompt, "必须给出 pacing 的具体规则"
    assert "其余内容保持原样" in prompt
    assert "不要重写整个故事线" in prompt
    # 未命中的类别不得出现
    assert CATEGORY_RULES["ooc"] not in prompt


def test_review_user_carries_outline_and_states() -> None:
    """★ 审校必须拿到细纲与状态表。

    否则它无法判断「是否落实细纲」「是否违反状态」，只能泛泛评论文笔 ——
    旧项目 Editor 的 `outline_deviation` 判定就缺少细纲原文对照。
    """
    text = build_review_user(
        GOOD_PROSE, plan_detail="1. 目标：开门", states="陈砚：左肩有伤",
        end_hook="周德海后退半步",
    )
    assert "开门" in text and "左肩有伤" in text and "周德海后退半步" in text


def test_review_user_notes_transition_chapter() -> None:
    """★ 换场章必须提前告知审校是正常的。

    旧项目把新地图一律记为「细纲偏离」（`editor.py:106`）→
    换图在写作层与审校层被**双重禁止**。
    """
    text = build_review_user(GOOD_PROSE, plan_detail="x", is_transition=True)
    assert "换场章" in text and "不算偏离" in text


# --------------------------------------------------------------------------- #
# P4-9 抽取：三个节点各抽一类
# --------------------------------------------------------------------------- #


def test_extractor_prompts_are_separate_and_narrow() -> None:
    """★ 三个抽取节点的 prompt 必须**各自只讲一类**。

    旧项目单条 prompt 超 10,000 字符、一次抽 11 类
    → `entity_classify` 空输出 **100%**。
    """
    # 场景抽取器**不抽关系**（只提一句"本节点不抽关系"作为边界说明）
    assert "relation" not in SCENE_EXTRACTOR_SYSTEM
    assert "surface_facing" not in SCENE_EXTRACTOR_SYSTEM
    # 关系抽取器只讲关系
    assert "actions" not in RELATION_EXTRACTOR_SYSTEM
    assert "injury_updates" not in RELATION_EXTRACTOR_SYSTEM
    # 归一器只讲对齐
    assert "summary" not in ENTITY_RESOLVER_SYSTEM
    assert "不要臆造 ID" in ENTITY_RESOLVER_SYSTEM


def test_relation_extractor_prompt_forbids_actions() -> None:
    """★ 「不要把动作当成关系」必须写进 prompt。

    旧项目把动作写进关系表 → 966 条关系里 **86.7% 不是人物-人物**。
    """
    assert "不要把动作当成关系" in RELATION_EXTRACTOR_SYSTEM
    assert "86.7%" in RELATION_EXTRACTOR_SYSTEM


def test_validate_relations_catches_action_as_entity() -> None:
    """★ 零 LLM 防线：动作词冒充关系端点必须被抓。"""
    out = RelationExtractionOut(relations=[
        RelationUpdateOut(from_entity="陈砚", to_entity="查看", kind="师徒"),
    ])
    problems = validate_relations(out)
    assert problems
    assert any("动作" in p for p in problems)


def test_validate_relations_catches_verb_phrase() -> None:
    out = RelationExtractionOut(relations=[
        RelationUpdateOut(
            from_entity="陈砚站在门口看着周德海把钥匙插进锁孔里转动",
            to_entity="周德海", kind="师徒",
        ),
    ])
    assert any("过长" in p for p in validate_relations(out))


def test_validate_relations_passes_normal() -> None:
    out = RelationExtractionOut(relations=[
        RelationUpdateOut(from_entity="陈砚", to_entity="周德海",
                          kind="盟友", surface_facing="师徒", true_facing="互相提防",
                          is_mutual=False),
    ])
    assert validate_relations(out) == []


def test_validate_scene_requires_death_evidence() -> None:
    """★ 报死亡必须有事件支撑（漏记会让角色复活，多记会让角色消失）。"""
    out = SceneExtractionOut(summary="有人死了", deaths=["老梁"])
    assert any("事件依据" in p for p in validate_scene(out))


def test_validate_scene_requires_heal_evidence() -> None:
    """★ 报痊愈必须有**结构化节拍**支撑 —— 这是旧项目「关键词自锁」的替代。

    旧项目要求同一次 LLM 输出的 events 里同时含角色名 + 治疗语义词，
    而 prompt 只让写 2~5 条关键事件 → 过渡章必然自锁 → 恢复率 0%。
    改用结构化证据后：无节拍 = 无据恢复（拒绝），有节拍 = 通过（不自锁）。
    """
    from storyforge.domain.enums import InjuryKind, InjurySeverity, InjuryStatus
    from storyforge.domain.health import HealthBeat, Injury
    from storyforge.domain.enums import HealthAction

    injury = Injury(
        id=7, book_id=1, entity_id=1, kind=InjuryKind.WOUND, name="左肩剑创",
        severity=InjurySeverity.SEVERE, status=InjuryStatus.NEW,
        since_chapter=1, updated_chapter=1,
    )
    out = SceneExtractionOut(
        summary="伤好了",
        events=[EventOut(description="陈砚休息了一晚")],
        injury_updates=[InjuryUpdateOut(
            entity="陈砚", name="左肩剑创", kind="伤势", severity="重",
            status=InjuryStatus.HEALED.value,
        )],
    )
    # ① 无节拍 → 拒绝恢复申报
    problems = validate_scene(
        out, planned_beats=[], global_no=5, scene_count=4, injuries=[injury]
    )
    assert any("恢复必须由场景承载" in p for p in problems)

    # ② 有匹配节拍且场景真实 → 通过（**不自锁**）
    beats = [HealthBeat(injury_id=7, action=HealthAction.HEALED,
                        chapter_no=5, scene_seq=2, note="痊愈")]
    ok = validate_scene(
        out, planned_beats=beats, global_no=5, scene_count=4, injuries=[injury]
    )
    assert ok == [], f"有结构化证据时不得拒绝（防自锁）：{ok}"


def test_validate_scene_rejects_beat_pointing_outside_scenes() -> None:
    """节拍指向的场景必须真实存在（否则仍是凭空痊愈）。"""
    from storyforge.domain.enums import HealthAction, InjuryKind, InjurySeverity, InjuryStatus
    from storyforge.domain.health import HealthBeat, Injury

    injury = Injury(
        id=7, book_id=1, entity_id=1, kind=InjuryKind.WOUND, name="左肩剑创",
        severity=InjurySeverity.SEVERE, status=InjuryStatus.NEW,
        since_chapter=1, updated_chapter=1,
    )
    out = SceneExtractionOut(
        summary="伤好了",
        injury_updates=[InjuryUpdateOut(
            entity="陈砚", name="左肩剑创", kind="伤势", severity="重",
            status=InjuryStatus.HEALED.value,
        )],
    )
    beats = [HealthBeat(injury_id=7, action=HealthAction.HEALED,
                        chapter_no=5, scene_seq=9, note="痊愈")]
    problems = validate_scene(
        out, planned_beats=beats, global_no=5, scene_count=4, injuries=[injury]
    )
    assert any("场景" in p for p in problems)


def test_validate_scene_passes_complete() -> None:
    out = SceneExtractionOut(
        summary="陈砚开门",
        events=[EventOut(description="陈砚打开了档案室的门")],
        entities=[EntityMentionOut(name="陈砚", kind="人物")],
    )
    assert validate_scene(out) == []


def test_render_known_entities_empty_for_first_chapter() -> None:
    assert "第一章" in render_known_entities([])


def test_render_known_entities_lists_ids_and_aliases() -> None:
    text = render_known_entities([(3, "陈砚", ["小陈", "砚哥"])])
    assert "3" in text and "陈砚" in text and "砚哥" in text


# --------------------------------------------------------------------------- #
# P4-13 C-7 止损
# --------------------------------------------------------------------------- #


def test_c7_triggers_on_write_failure() -> None:
    """写作失败（拒答/空输出）→ 停下等人工，不自动烧钱重试。"""
    assert needs_c7_confirm({"status": "write_failed"}) is True


def test_c7_triggers_when_rewrites_exhausted() -> None:
    """★ 重写达上限仍不达标 → C-7（旧项目无此闸门，重写空转无人统计）。"""
    assert needs_c7_confirm(
        {"rewrite_count": 3, "needs_review": True}, max_rewrites=3
    ) is True


def test_c7_not_triggered_while_rewrites_remain() -> None:
    assert needs_c7_confirm(
        {"rewrite_count": 1, "needs_review": True}, max_rewrites=3
    ) is False


def test_c7_not_triggered_on_clean_pass() -> None:
    assert needs_c7_confirm(
        {"rewrite_count": 0, "needs_review": False}, max_rewrites=3
    ) is False


def test_c7_confirm_point_exists() -> None:
    assert ConfirmPoint.C7_QUALITY_GATE.value == "C7_quality_gate"


# --------------------------------------------------------------------------- #
# P4-11 图能编译 + 干跑（就绪闸门干净退出）
# --------------------------------------------------------------------------- #


def test_chapter_graph_compiles(tmp_path) -> None:
    deps = ChapterDeps(client=None, store=ArtifactStore(tmp_path))
    g = build_chapter_graph(deps)
    nodes = set(g.get_graph().nodes)
    assert {"readiness", "write", "score", "review", "revise",
            "extract", "commit"} <= nodes


async def test_chapter_graph_exits_cleanly_when_not_ready(tmp_path) -> None:
    """★ 细纲未就绪 → **干净退出**（status=not_ready），不是异常。

    若这里抛错，协调器会把该阶段 worker 打成 FAILED ——
    而"细纲还没轮到"是正常的调度状态。
    """
    store = ArtifactStore(tmp_path)
    deps = ChapterDeps(client=None, store=store)  # 不调 LLM 就不会用到 client
    g = build_chapter_graph(deps)

    out = await g.ainvoke({"book_id": 1, "global_no": 10,
                           "detail_ready_through": 0, "refs": {}})
    assert out["status"] == NOT_READY
    assert "尚未就绪" in out["error"]


async def test_chapter_graph_blocks_when_plan_should_exist(tmp_path) -> None:
    """细纲该有却没有 → `blocked`（留痕，不静默）"""
    store = ArtifactStore(tmp_path)
    g = build_chapter_graph(ChapterDeps(client=None, store=store))
    out = await g.ainvoke({"book_id": 1, "global_no": 5,
                           "detail_ready_through": 10, "refs": {}})
    assert out["status"] == BLOCKED


async def test_chapter_graph_full_loop_with_stubs(tmp_path) -> None:
    """★ 端到端干跑：写作 → 打分 → 抽取 → 提交（用脚本化 transport）。

    这条用例证明整图**连通**，而不只是"能编译"。
    """
    store = ArtifactStore(tmp_path)
    persist_plan(store, 1, plan(1))
    store.write_text("books/1/bible.md", "【世界观】长明楼。")

    scene_json = SceneExtractionOut(
        summary="陈砚开门",
        events=[EventOut(description="陈砚打开了档案室的门")],
        entities=[EntityMentionOut(name="陈砚", kind="人物")],
    ).model_dump_json()
    resolve_json = (
        '{"mentions":["陈砚"],"links":{},"new_canonical":{"陈砚":"陈砚"}}'
    )
    relations_json = RelationExtractionOut(relations=[
        RelationUpdateOut(from_entity="陈砚", to_entity="周德海", kind="盟友"),
    ]).model_dump_json()
    # 第 1 章是黄金三章 → 无条件送审。这里让审校**通过且无问题**，
    # 以便完整走完 写作 → 打分 → 审校 → 抽取 → 提交。
    review_json = ReviewResultOut(issues=[], score=9.0).model_dump_json()

    client, _ = make_client({
        "chapter_writer": GOOD_PROSE,
        "style_critic": review_json,
        "scene_extractor": scene_json,
        "entity_resolver": resolve_json,
        "relation_extractor": relations_json,
    })
    g = build_chapter_graph(ChapterDeps(client=client, store=store))

    out = await g.ainvoke({
        "book_id": 1, "global_no": 1, "volume_no": 1,
        "detail_ready_through": 1,
        "refs": {"bible_ref": "books/1/bible.md"},
        "states_block": "陈砚：在长明楼",
        "known_entities": [],
    })
    assert out["status"] == "committed", out.get("error")
    assert out["prose_ref"] == "books/1/prose/00001.txt"
    assert out["prose_chars"] > 0
    assert out["extraction_ref"] == "books/1/extractions/00001.json"
    assert out["committed"] is True
    assert load_prose(store, 1, 1).strip() == GOOD_PROSE.strip()
    assert store.exists("books/1/extractions/00001.json")


async def test_chapter_graph_commit_refuses_without_extraction(tmp_path) -> None:
    """★ 缺抽取产物 → **拒绝提交**（记忆账本会与正文脱节）。

    「缺省即不通过」：没有入库就不报成功。
    """
    store = ArtifactStore(tmp_path)
    persist_plan(store, 1, plan(1))
    store.write_text("books/1/bible.md", "x")
    persist_prose(store, 1, 1, GOOD_PROSE)

    from storyforge.graph.graphs.chapter_nodes import make_commit_node

    node = make_commit_node(ChapterDeps(client=None, store=store))
    out = await node({"book_id": 1, "global_no": 1})
    assert out["committed"] is False
    assert "抽取产物" in out["error"]
