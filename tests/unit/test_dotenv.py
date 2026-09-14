""".env 密钥加载测试。

## 本文件防的是什么

1. **静默覆盖** —— 旧项目 `.env` 写的模型被 `llm_providers.json` 悄悄盖掉，
   且没有任何提示。因此本项目的 `.env` **不得覆盖真实环境变量**。
2. **密钥泄漏** —— `load_dotenv` 只回传**键名**，永不回传值；
   这样调用方可以放心把返回值打印进日志。
3. **格式脆弱** —— `.env` 常被手工编辑，一行笔误不该让整个程序起不来。
"""

from __future__ import annotations

import os

import pytest

from storyforge import config as cfg

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    """把 `dotenv_paths()` 指向临时文件。"""
    p = tmp_path / ".env"
    monkeypatch.setattr(cfg, "dotenv_paths", lambda: [p])
    return p


def test_loads_simple_key_value(fake_env, monkeypatch) -> None:
    monkeypatch.delenv("SF_TEST_A", raising=False)
    fake_env.write_text("SF_TEST_A=hello\n", encoding="utf-8")
    injected = cfg.load_dotenv()
    assert "SF_TEST_A" in injected
    assert os.environ["SF_TEST_A"] == "hello"


def test_returns_keys_not_values(fake_env, monkeypatch) -> None:
    """★ 只回传键名 —— 密钥绝不能出现在返回值里（会被打印进日志）。"""
    monkeypatch.delenv("SF_TEST_SECRET", raising=False)
    fake_env.write_text("SF_TEST_SECRET=sk-super-secret-value\n", encoding="utf-8")
    injected = cfg.load_dotenv()
    assert injected == ["SF_TEST_SECRET"]
    assert "sk-super-secret-value" not in str(injected)


def test_does_not_override_real_env(fake_env, monkeypatch) -> None:
    """★ 真实环境变量优先 —— 防旧项目那种「文件静默覆盖」的坑。"""
    monkeypatch.setenv("SF_TEST_B", "from-shell")
    fake_env.write_text("SF_TEST_B=from-file\n", encoding="utf-8")
    injected = cfg.load_dotenv()
    assert "SF_TEST_B" not in injected
    assert os.environ["SF_TEST_B"] == "from-shell"


def test_override_true_when_explicitly_asked(fake_env, monkeypatch) -> None:
    monkeypatch.setenv("SF_TEST_C", "from-shell")
    fake_env.write_text("SF_TEST_C=from-file\n", encoding="utf-8")
    cfg.load_dotenv(override=True)
    assert os.environ["SF_TEST_C"] == "from-file"


def test_supports_export_prefix_and_quotes(fake_env, monkeypatch) -> None:
    monkeypatch.delenv("SF_TEST_D", raising=False)
    monkeypatch.delenv("SF_TEST_E", raising=False)
    fake_env.write_text(
        'export SF_TEST_D="quoted value"\nSF_TEST_E=\'single\'\n', encoding="utf-8"
    )
    cfg.load_dotenv()
    assert os.environ["SF_TEST_D"] == "quoted value"
    assert os.environ["SF_TEST_E"] == "single"


def test_skips_comments_and_blanks(fake_env, monkeypatch) -> None:
    monkeypatch.delenv("SF_TEST_F", raising=False)
    fake_env.write_text(
        "# 这是注释\n\n   \nSF_TEST_F=ok\n# SF_TEST_F=commented\n", encoding="utf-8"
    )
    injected = cfg.load_dotenv()
    assert injected == ["SF_TEST_F"]
    assert os.environ["SF_TEST_F"] == "ok"


def test_malformed_line_is_skipped_not_fatal(fake_env, monkeypatch) -> None:
    """★ 一行笔误不该让整个程序起不来（`.env` 是手工编辑的）。"""
    monkeypatch.delenv("SF_TEST_G", raising=False)
    fake_env.write_text("这行没有等号\nSF_TEST_G=ok\n=没有键\n", encoding="utf-8")
    injected = cfg.load_dotenv()
    assert injected == ["SF_TEST_G"]


def test_missing_file_is_not_an_error(tmp_path, monkeypatch) -> None:
    """CI 里通常没有 .env —— 缺文件必须返回空列表，不是异常。"""
    monkeypatch.setattr(cfg, "dotenv_paths", lambda: [tmp_path / "nope.env"])
    assert cfg.load_dotenv() == []


def test_value_may_contain_equals(fake_env, monkeypatch) -> None:
    """值里含 `=` 时必须完整保留（base64 key 常见）。"""
    monkeypatch.delenv("SF_TEST_H", raising=False)
    fake_env.write_text("SF_TEST_H=abc=def==\n", encoding="utf-8")
    cfg.load_dotenv()
    assert os.environ["SF_TEST_H"] == "abc=def=="


def test_empty_value_is_allowed(fake_env, monkeypatch) -> None:
    """模板里留空的 key（如 `.env.example` 的 `MIMO_API_KEY=`）应当能加载。"""
    monkeypatch.delenv("SF_TEST_I", raising=False)
    fake_env.write_text("SF_TEST_I=\n", encoding="utf-8")
    cfg.load_dotenv()
    assert os.environ["SF_TEST_I"] == ""


def test_dotenv_paths_points_at_repo_root() -> None:
    """默认只找仓库根的 `.env`（可预测，不随 cwd 漂移）。"""
    paths = cfg.dotenv_paths()
    assert paths and paths[0].name == ".env"
    assert paths[0].parent == cfg.REPO_ROOT


def test_env_example_lists_all_provider_keys() -> None:
    """★ `.env.example` 必须覆盖 `providers.toml` 里声明的**全部** key 变量。

    防的是"新增 provider 却忘了更新模板" —— 那会让使用者
    面对一个没有提示的缺失（旧项目正是靠人记住有哪些 key）。
    """
    from storyforge.infra.llm.capability import load_registry

    example = (cfg.REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    reg = load_registry()
    missing = [
        p.api_key_env for p in reg.providers.values()
        if p.api_key_env and p.api_key_env not in example
    ]
    assert not missing, f".env.example 缺这些 key 变量：{missing}"


def test_env_is_gitignored() -> None:
    """★ `.env` 必须被忽略（旧项目 13 个含密钥的 bak 文件裸奔过）。"""
    gi = (cfg.REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gi
    assert "!.env.example" in gi, "模板文件必须显式放行"
