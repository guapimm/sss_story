"""`storyforge flows` 命令测试 —— learn 的「流派可多选」**可视化入口**。

## 为什么这条命令值得测试

用户明确要求 learn「**全流程用户可视化，避免出现之前的黑盒情况**」。
目标流程第二步是「选研究主题和小说题材，流派**可多选**」——
用户必须先**看到**可选清单与每张卡的内容才能做选择。

若这个入口坏了（清单不全、详情卡不出、多选顺序乱），
用户就只能盲选 → 又回到黑盒。

本文件断言的是**用户实际会看到的输出**，不是内部对象。
"""

from __future__ import annotations

import pytest

from storyforge.cli import build_parser, cmd_flows

pytestmark = pytest.mark.unit


class _Args:
    """极简 args（`cmd_flows` 只读这几个属性）。"""

    def __init__(self, namespace="all", detail=None, with_example=False) -> None:
        self.namespace = namespace
        self.detail = detail
        self.with_example = with_example


def test_cli_has_flows_command() -> None:
    """`flows` 子命令必须在（它是唯一能看到流派清单的入口）。"""
    p = build_parser()
    ns = p.parse_args(["flows"])
    assert getattr(ns, "func", None) is cmd_flows


def test_flows_lists_all_25_xianxia(capsys) -> None:
    """★ 修仙流派必须**全部**列出（25 个）—— 用户要从中多选。"""
    rc = cmd_flows(_Args(namespace="xianxia"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "共 25 个" in out
    for fid in ("knowledge_seeker", "sword_saint", "mortal", "villain"):
        assert fid in out, f"清单里缺流派 {fid}"


def test_flows_lists_both_namespaces_by_default(capsys) -> None:
    """默认 `-n all` 两个命名空间都要出（tropes 16 + xianxia 25）。"""
    rc = cmd_flows(_Args(namespace="all"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "══ tropes ══" in out
    assert "══ xianxia ══" in out
    assert "共 16 个" in out
    assert "共 25 个" in out


def test_flows_detail_prints_full_card(capsys) -> None:
    """`--detail` 必须打印**完整卡**（卖点/结构/气质/装置）。"""
    rc = cmd_flows(_Args(namespace="xianxia", detail=["knowledge_seeker"]))
    out = capsys.readouterr().out
    assert rc == 0
    for label in ("【流派】悟道流", "卖点：", "叙事结构：", "主角气质：", "叙事装置"):
        assert label in out, f"完整卡缺 {label}"


def test_flows_detail_multi_select_preserves_order(capsys) -> None:
    """★ **多选**：传多个 id 时按传入顺序输出（顺序即权重顺序）。

    这是 learn 目标流程「流派可多选」的直接验证。
    """
    sel = ["sword_saint", "knowledge_seeker"]
    rc = cmd_flows(_Args(namespace="xianxia", detail=sel))
    out = capsys.readouterr().out
    assert rc == 0
    i_sword = out.index("xianxia/sword_saint")
    i_know = out.index("xianxia/knowledge_seeker")
    assert i_sword < i_know, "多选顺序未被保持（应是 sword_saint 在前）"


def test_flows_detail_with_example_toggle(capsys) -> None:
    """`--with-example` 控制是否带上 arc_example（风格示例）。"""
    rc = cmd_flows(_Args(namespace="xianxia", detail=["knowledge_seeker"]))
    without = capsys.readouterr().out
    assert rc == 0
    rc = cmd_flows(_Args(namespace="xianxia", detail=["knowledge_seeker"],
                         with_example=True))
    with_ex = capsys.readouterr().out
    assert rc == 0
    assert "风格示例" in with_ex
    assert "风格示例" not in without
    assert len(with_ex) > len(without)


def test_flows_unknown_id_reports_error(capsys) -> None:
    """未知流派 id → 退出码 2 + stderr 提示（不静默）。"""
    rc = cmd_flows(_Args(namespace="xianxia", detail=["不存在"]))
    assert rc == 2
    err = capsys.readouterr().err
    assert "找不到流派" in err


def test_flows_is_offline_and_free() -> None:
    """`flows` 命令必须**零 LLM、零网络**（它只是读 TOML）。

    防的是"看个清单也要花钱/要 API key"—— 那会让可视化入口在
    未配置密钥时不可用。
    """
    import inspect

    src = inspect.getsource(cmd_flows)
    for forbidden in ("make_transport", "LLMClient", "get_settings().llm",
                      "async def", "await "):
        assert forbidden not in src, (
            f"`cmd_flows` 里出现了 {forbidden!r} —— 该命令必须离线且零 LLM"
        )


# ---------------------------------------------------------------------------
# PG-1C（B-8）：资源缺失必须大声报错，不得伪装成"目录为空"退出码 0
# ---------------------------------------------------------------------------


def test_flows_missing_resource_exits_nonzero(tmp_path, monkeypatch, capsys) -> None:
    """★ 命名空间目录**不存在**（安装不完整）→ 退出码 2 + stderr 明确报错。

    PG-0 实测（B-8）：安装态 `flows -n xianxia` 打印"（目录为空或不存在）"
    且**退出码 0** —— 把损坏伪装成正常，是本项目明令禁止的静默降级。
    数据层 `load_namespace` 的 `{}` 语义不动（doc/11 §2.2），
    用户可见层在这里区分两种状态。
    """
    from storyforge.config import reset_settings

    monkeypatch.setenv("STORYFORGE_CONFIG_DIR", str(tmp_path / "no_such_config"))
    reset_settings()  # get_settings() 有缓存，不重置会读到上一个测试的 config_dir
    try:
        rc = cmd_flows(_Args(namespace="xianxia"))
        assert rc == 2, "资源缺失竟返回 0 —— B-8 回归（静默降级）"
        err = capsys.readouterr().err
        assert "资源缺失" in err
        assert "xianxia" in err
    finally:
        reset_settings()


def test_flows_all_reports_missing_when_any_namespace_absent(
    tmp_path, monkeypatch, capsys
) -> None:
    """`-n all` 时任一命名空间缺失都要报错退出码 2（两个都是必需资源）。"""
    from storyforge.config import reset_settings

    cfg = tmp_path / "cfg"
    (cfg / "flows" / "tropes").mkdir(parents=True)  # tropes 在，xianxia 缺
    monkeypatch.setenv("STORYFORGE_CONFIG_DIR", str(cfg))
    reset_settings()
    try:
        rc = cmd_flows(_Args(namespace="all"))
        assert rc == 2
        err = capsys.readouterr().err
        assert "xianxia" in err and "资源缺失" in err
    finally:
        reset_settings()


def test_flows_empty_dir_is_legal_and_printed_as_such(
    tmp_path, monkeypatch, capsys
) -> None:
    """目录**存在但为空** → 合法状态（全新 clone 未填充）→ 退出码 0。

    与上一条共同把两种状态分开钉死：缺失≠为空（doc/11 §2.2 的语义在
    用户可见层的对应形态）。
    """
    from storyforge.config import reset_settings

    cfg = tmp_path / "cfg"
    (cfg / "flows" / "xianxia").mkdir(parents=True)
    monkeypatch.setenv("STORYFORGE_CONFIG_DIR", str(cfg))
    reset_settings()
    try:
        rc = cmd_flows(_Args(namespace="xianxia"))
        assert rc == 0
        out, err = capsys.readouterr()
        assert "目录存在但为空" in out
        assert "资源缺失" not in out + err
    finally:
        reset_settings()
