"""pytest 全局夹具 —— **隔离是第一要求**。

## 为什么这个文件很重要

旧项目最大的测试基建缺陷：**121 / 123 个测试文件在模块级直接改 `os.environ`**
（根因是 `config.py` 在**类定义期**冻结 settings，测试不得不在 import 前改环境）。
后果是单进程 `pytest` 先 import 全部模块再跑用例 → **所有用例都跑在
「最后被 import 的模块」留下的环境上** → 实测产生 3 failed + 8 errors 的**假失败**。

本项目的对策有两层：
  1. `config.py` 提供**运行时注入**（`configure()`），测试不需要碰环境变量；
  2. 本文件提供统一夹具，**每个测试独立临时 DB + 独立配置**，用完即清。

因此本仓库**禁止**在测试模块级修改 `os.environ`（由 contract 测试强制）。
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from storyforge.config import Settings, configure, reset_settings
from storyforge.infra.storage.db import Database


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Iterator[Settings]:
    """每个测试独立的配置（数据目录指向临时目录）。"""
    reset_settings()
    s = configure(
        Settings(
            data_dir=tmp_path / "data",
            config_dir=Path(__file__).resolve().parents[1] / "config",
        )
    )
    s.ensure_dirs()
    yield s
    reset_settings()


@pytest.fixture
def db(tmp_settings: Settings) -> Iterator[Database]:
    """每个测试独立临时数据库（含外键强制）。

    ⚠️ 绝不使用真实库 —— 旧项目测试污染真库是反复出现的问题。
    """
    database = Database.from_settings(tmp_settings)
    database.create_all()
    yield database
    database.drop_all()


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """临时目录（放产物文件）。"""
    return tmp_path


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def src_root(repo_root: Path) -> Path:
    return repo_root / "src" / "storyforge"
