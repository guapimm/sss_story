"""`storyforge learn-map` 命令测试 —— L-1/L-2 的**编辑 + 回退**闭环。

## 为什么这个文件是 2-3 的验收核心

用户对确认粒度的原话要求是「能**编辑 + 回退**」。
这不是"有个确认按钮"就行 —— 要满足四个行为：

  ① 逐条确认（改一条不影响其它）
  ② 改完**自动取消**该条确认（否则"已确认"没意义）
  ③ 回退能回到**指定版本**（内容与确认状态都回去）
  ④ 确认前**过校验**（有问题的地图不能盖章通过）

②③ 尤其容易在重构里退化（都是"隐式行为"，不看代码看不出来），
所以用测试钉死。

⚠️ 全部离线：用 `tmp_settings` 夹具隔离产物目录，不碰真实 data/。
"""

from __future__ import annotations

import json

import pytest

from storyforge.commands.learning import cmd_learn_map
from storyforge.infra.storage.artifacts import ArtifactStore

pytestmark = pytest.mark.unit


class _Args:
    def __init__(self, action: str, **kw) -> None:
        self.action = action
        self.book_id = kw.pop("book_id", 1)
        self.topic = kw.pop("topic", "")
        self.scale = kw.pop("scale", "standard")
        self.genre = kw.pop("genre", "xianxia")
        self.points = kw.pop("points", False)
        self.mappings = kw.pop("mappings", None)
        self.term = kw.pop("term", "")
        self.replacement = kw.pop("replacement", "")
        self.revision = kw.pop("revision", 0)
        self.force = kw.pop("force", False)
        self.verbose = kw.pop("verbose", False)
        assert not kw, f"未消费的参数：{kw}"


def _seed_map(book_id: int = 1) -> None:
    """直接写一份地图（跳过 gen，聚焦确认流程）。"""
    store = ArtifactStore.from_settings()
    store.write_json(
        ArtifactStore.learning_map_key(book_id),
        {
            "topic": "宋代点茶",
            "scale": "fast",
            "points": [
                {"point_id": "kp001", "name": "点茶的起源",
                 "source_urls": ["https://a"], "evidence": "…"},
                {"point_id": "kp002", "name": "茶筅与击拂",
                 "source_urls": ["https://b"], "evidence": "…"},
            ],
            "mappings": [
                {"term": "函数", "replacement": "自创功法", "accepted": False},
                {"term": "循环", "replacement": "轮回诀", "accepted": False},
                {"term": "报错/异常", "replacement": "心魔反噬", "accepted": False},
            ],
            "research_shortfall": "",
        },
    )


def _read(book_id: int = 1) -> dict:
    store = ArtifactStore.from_settings()
    return json.loads(store.read_text(ArtifactStore.learning_map_key(book_id)))


def _accepted(book_id: int = 1) -> set[str]:
    return {m["term"] for m in _read(book_id)["mappings"] if m["accepted"]}


# --------------------------------------------------------------------------- #
# show
# --------------------------------------------------------------------------- #


def test_show_lists_both_confirm_layers(tmp_path, tmp_settings, capsys) -> None:
    """★ `show` 必须同时展示 **L-1 知识点**与 **L-2 平替修辞**（两个确认点）。"""
    _seed_map()
    rc = cmd_learn_map(_Args("show"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "L-1 知识点" in out
    assert "L-2 平替修辞" in out
    assert "点茶的起源" in out
    assert "函数 → 自创功法" in out


def test_show_marks_missing_source(tmp_path, tmp_settings, capsys) -> None:
    """★ 无来源的知识点必须显式标注（可能是幻觉）。"""
    store = ArtifactStore.from_settings()
    store.write_json(
        ArtifactStore.learning_map_key(1),
        {"topic": "t", "scale": "fast",
         "points": [{"point_id": "kp1", "name": "无来源", "source_urls": []}],
         "mappings": []},
    )
    cmd_learn_map(_Args("show"))
    out = capsys.readouterr().out
    assert "无来源" in out
    assert "校验" in out


def test_show_without_map_gives_actionable_error(tmp_path, tmp_settings, capsys) -> None:
    """没有地图时必须给出**可操作的**指引（不是一句"找不到"）。"""
    with pytest.raises(SystemExit) as ei:
        cmd_learn_map(_Args("show"))
    assert "learn-map gen" in str(ei.value)


# --------------------------------------------------------------------------- #
# accept（两层）
# --------------------------------------------------------------------------- #


def test_accept_points_layer(tmp_path, tmp_settings, capsys) -> None:
    """L-1：确认知识点集合。"""
    _seed_map()
    rc = cmd_learn_map(_Args("accept", points=True))
    assert rc == 0
    assert "L-1" in capsys.readouterr().out


def test_accept_specific_mappings_only(tmp_path, tmp_settings, capsys) -> None:
    """★★ **逐条确认**（用户决策的粒度）：只确认指定的几条，其它不动。"""
    _seed_map()
    rc = cmd_learn_map(_Args("accept", mappings=["函数", "循环"]))
    assert rc == 0
    assert _accepted() == {"函数", "循环"}, (
        "逐条确认失效 —— 要么全确认了，要么没确认"
    )


def test_accept_unknown_term_fails(tmp_path, tmp_settings, capsys) -> None:
    _seed_map()
    rc = cmd_learn_map(_Args("accept", mappings=["不存在的术语"]))
    assert rc == 1
    assert "找不到" in capsys.readouterr().err


def test_accept_requires_a_layer(tmp_path, tmp_settings, capsys) -> None:
    """两个层都不指定 → 退出码 2 + 明确提示（不静默什么都不做）。"""
    _seed_map()
    rc = cmd_learn_map(_Args("accept"))
    assert rc == 2
    assert "--points" in capsys.readouterr().err


def test_accept_points_blocked_by_validation(tmp_path, tmp_settings, capsys) -> None:
    """★★ 有问题的地图**不能盖章通过** —— 它是世界观的输入，错误会放大。"""
    store = ArtifactStore.from_settings()
    store.write_json(
        ArtifactStore.learning_map_key(1),
        {"topic": "t", "scale": "fast", "points": [],
         "mappings": [{"term": "函数", "replacement": "函数"}]},
    )
    rc = cmd_learn_map(_Args("accept", points=True))
    assert rc == 1, "空知识点集应被校验拦下"
    assert "拒绝确认" in capsys.readouterr().err


def test_accept_force_bypasses_validation(tmp_path, tmp_settings, capsys) -> None:
    """`--force` 可强制通过（给人留出口，但要显式）。"""
    store = ArtifactStore.from_settings()
    store.write_json(
        ArtifactStore.learning_map_key(1),
        {"topic": "t", "scale": "fast", "points": [], "mappings": []},
    )
    rc = cmd_learn_map(_Args("accept", points=True, force=True))
    assert rc == 0


# --------------------------------------------------------------------------- #
# edit（★ 用户要求的"能编辑"）
# --------------------------------------------------------------------------- #


def test_edit_changes_replacement(tmp_path, tmp_settings, capsys) -> None:
    """★ 改一条映射的世界内说法。"""
    _seed_map()
    rc = cmd_learn_map(_Args("edit", term="函数", replacement="本命功法"))
    assert rc == 0
    mp = next(m for m in _read()["mappings"] if m["term"] == "函数")
    assert mp["replacement"] == "本命功法"
    assert "本命功法" in capsys.readouterr().out


def test_edit_auto_unconfirms_the_entry(tmp_path, tmp_settings, capsys) -> None:
    """★★ 改动后**自动取消该条确认**，且**只影响该条**。

    为什么必须这样：改完还标着"已确认"会让确认状态失去意义
    （用户以为确认过的是新说法，实际确认的是旧说法）。
    且取消范围必须**限于被改的那条** —— 连坐会让"逐条确认"退化。
    """
    _seed_map()
    cmd_learn_map(_Args("accept", mappings=["函数", "循环"]))
    assert _accepted() == {"函数", "循环"}

    cmd_learn_map(_Args("edit", term="函数", replacement="本命功法"))

    after = _accepted()
    assert "函数" not in after, "改过的条目必须自动取消确认"
    assert "循环" in after, "★ 未被改动的条目不该被连坐取消"


def test_edit_unknown_term_fails(tmp_path, tmp_settings, capsys) -> None:
    _seed_map()
    rc = cmd_learn_map(_Args("edit", term="不存在", replacement="x"))
    assert rc == 1
    assert "找不到" in capsys.readouterr().err


def test_edit_requires_replacement(tmp_path, tmp_settings, capsys) -> None:
    _seed_map()
    rc = cmd_learn_map(_Args("edit", term="函数"))
    assert rc == 2
    assert "--replacement" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# reject
# --------------------------------------------------------------------------- #


def test_reject_bumps_revision_and_unconfirms(tmp_path, tmp_settings, capsys) -> None:
    """`reject` 记 revision（观测"哪些条目难产"）并取消确认。"""
    _seed_map()
    cmd_learn_map(_Args("accept", mappings=["函数"]))
    rc = cmd_learn_map(_Args("reject", term="函数"))
    assert rc == 0
    mp = next(m for m in _read()["mappings"] if m["term"] == "函数")
    assert mp["revision"] == 1
    assert mp["accepted"] is False


def test_reject_is_idempotent_in_counting(tmp_path, tmp_settings) -> None:
    """多次 reject 累计 revision（每次都是"还没改好"的信号）。"""
    _seed_map()
    for _ in range(3):
        cmd_learn_map(_Args("reject", term="循环"))
    mp = next(m for m in _read()["mappings"] if m["term"] == "循环")
    assert mp["revision"] == 3


# --------------------------------------------------------------------------- #
# ★★ rollback（用户要求的"能回退"）
# --------------------------------------------------------------------------- #


def test_rollback_restores_content_and_confirmation(
    tmp_path, tmp_settings, capsys
) -> None:
    """★★★ **回退必须同时恢复内容与确认状态**。

    这是"能回退"的完整含义 —— 只恢复内容而丢了确认状态，
    用户要重新确认一遍；只恢复确认状态而内容还是错的，更糟。

    场景：确认两条 → 改一条（自动取消）→ 回退 → 应回到"两条都确认、
    且说法是原来的"。
    """
    _seed_map()
    cmd_learn_map(_Args("accept", mappings=["函数", "循环"]))
    v_before_edit = max(_revisions())

    cmd_learn_map(_Args("edit", term="函数", replacement="本命功法"))
    assert "函数" not in _accepted()          # 编辑后确实变了

    rc = cmd_learn_map(_Args("rollback", revision=v_before_edit))
    assert rc == 0

    state = _read()
    mp = next(m for m in state["mappings"] if m["term"] == "函数")
    assert mp["replacement"] == "自创功法", "回退未恢复内容"
    assert _accepted() == {"函数", "循环"}, "回退未恢复确认状态"


def test_rollback_to_nonexistent_version_fails(tmp_path, tmp_settings, capsys) -> None:
    """★ 有历史版本、但指定的版号不存在 → 列出**可用的版号**。

    ⚠️ 必须**先制造历史**：`_seed_map()` 直接写文件、不产生存档，
    所以它之后 rollback 会先命中"没有历史版本"分支（那是另一条测试）。
    初版这里漏了这一步，测到的是错的分支。
    """
    _seed_map()
    cmd_learn_map(_Args("edit", term="函数", replacement="改一"))   # 制造第 1 版
    assert _revisions(), "应已产生历史版本"
    capsys.readouterr()

    rc = cmd_learn_map(_Args("rollback", revision=999))
    assert rc == 1
    err = capsys.readouterr().err
    assert "没有第" in err
    assert "[1]" in err, "应列出可用版号"


def test_rollback_without_history_fails(tmp_path, tmp_settings, capsys) -> None:
    """没有历史版本时回退必须失败（不静默什么都不做）。"""
    _seed_map()
    rc = cmd_learn_map(_Args("rollback", revision=1))
    assert rc == 1
    assert "没有历史版本" in capsys.readouterr().err


def test_rollback_is_itself_rollbackable(tmp_path, tmp_settings) -> None:
    """★ 回退**本身也可回退**（回退前先存档当前版本）。

    否则误回退一次就永久丢掉当前进度 —— 那会让用户不敢用这个功能。
    """
    _seed_map()
    cmd_learn_map(_Args("edit", term="函数", replacement="第一次改"))
    n1 = max(_revisions())
    cmd_learn_map(_Args("edit", term="函数", replacement="第二次改"))
    n2 = max(_revisions())

    # 回退到第一次改之后
    cmd_learn_map(_Args("rollback", revision=n1))
    assert next(m for m in _read()["mappings"]
                if m["term"] == "函数")["replacement"] == "第一次改"

    # 再回退到第二次改之后（说明回退动作本身被存档了）
    cmd_learn_map(_Args("rollback", revision=n2))
    assert next(m for m in _read()["mappings"]
                if m["term"] == "函数")["replacement"] == "第二次改"


def _revisions(book_id: int = 1) -> list[int]:
    store = ArtifactStore.from_settings()
    from storyforge.commands.learning import _list_revisions

    return [n for n, _ in _list_revisions(store, book_id)]


# --------------------------------------------------------------------------- #
# history
# --------------------------------------------------------------------------- #


def test_history_lists_versions_with_counts(tmp_path, tmp_settings, capsys) -> None:
    """`history` 要给出每版的条目数与确认数（供用户挑回退目标）。"""
    _seed_map()
    cmd_learn_map(_Args("edit", term="函数", replacement="改一"))
    cmd_learn_map(_Args("edit", term="函数", replacement="改二"))
    capsys.readouterr()

    rc = cmd_learn_map(_Args("history"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "共 2 版" in out
    assert "知识点" in out and "修辞" in out


def test_history_empty_is_not_an_error(tmp_path, tmp_settings, capsys) -> None:
    _seed_map()
    rc = cmd_learn_map(_Args("history"))
    assert rc == 0
    assert "暂无历史版本" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 未知子命令
# --------------------------------------------------------------------------- #


def test_unknown_action_returns_2(tmp_path, tmp_settings, capsys) -> None:
    args = _Args("show")
    args.action = "不存在的动作"
    rc = cmd_learn_map(args)
    assert rc == 2
    assert "子命令" in capsys.readouterr().err
