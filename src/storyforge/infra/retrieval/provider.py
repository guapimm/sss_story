"""检索 provider —— Tavily（真）与 Mock（离线/无 key）。

## 为什么不做"按 key 自动选 provider"

旧项目是 `get_search_provider()` 里 `if settings.TAVILY_API_KEY else Mock`
—— **运行期分支**，后果是"有没有 key"会**静默改变行为**：
测试通过、生产退回 Mock 返回占位文本，而调用方无法区分。

本设计改为**显式构造**（`make_research_provider()` 是唯一的工厂，
且它读的是配置而非"key 有没有"）：

    · 有 key        → TavilyResearchProvider
    · 无 key 且显式要求 → MockResearchProvider（**并打印警告**）

⚠️ Mock 结果**带明确标记**（`[Mock]` 前缀 + `url=""`），
下游能识别"这不是真检索结果"，不会把占位文本当知识点来源。

## 网络容错

  · 超时/5xx 重试 `max_attempts` 次（网络抖动不该让整本书失败）
  · 重试仍失败 → 抛 `ResearchError`（**不静默返回空列表**：
    "没搜到"与"搜索失败"是两回事，后者必须让调用方知道）
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from storyforge.config import ResearchSettings, get_settings, resolve_secret
from storyforge.modes.protocols import ResearchResult, ResearchSnippet

logger = logging.getLogger(__name__)


class ResearchError(RuntimeError):
    """检索失败（网络/鉴权/限流）—— 与"搜到 0 条"不同。"""


class TavilyResearchProvider:
    """Tavily 联网检索（https://tavily.com）。

    逐字对照旧项目 `app/search/provider.py` 的 `TavilyProvider`，
    三处改进：

      1. 超时/5xx **重试**（旧项目 `raise_for_status()` 直接抛）
      2. 失败抛 `ResearchError` 而非静默空列表
      3. 结果转成 `ResearchSnippet`（领域类型），不透传裸 dict
    """

    def __init__(
        self,
        api_key: str,
        *,
        settings: ResearchSettings | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ResearchError(
                "TavilyResearchProvider 需要 api_key（空值请用 MockResearchProvider）"
            )
        self.api_key = api_key
        self.s = settings or get_settings().research
        self._client = client or httpx.Client(timeout=self.s.timeout_s)
        self._owns_client = client is None

    # -- 接口 ---------------------------------------------------------------

    def search(self, query: str, max_results: int = 5) -> list[ResearchSnippet]:
        """单次检索。**失败抛错**，不返回空列表。"""
        payload: dict[str, Any] = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": self.s.search_depth,
            "include_answer": False,
        }
        last: Exception | None = None
        for attempt in range(1, self.s.max_attempts + 1):
            try:
                resp = self._client.post(self.s.endpoint, json=payload)
                if resp.status_code >= 500:
                    # 5xx 是服务端问题，重试有意义
                    raise httpx.HTTPStatusError(
                        f"服务端 {resp.status_code}", request=resp.request,
                        response=resp,
                    )
                resp.raise_for_status()
                data = resp.json()
                return [
                    ResearchSnippet(
                        title=str(r.get("title", "")),
                        url=str(r.get("url", "")),
                        text=str(r.get("content", "")),
                    )
                    for r in (data.get("results") or [])
                ]
            except (httpx.TimeoutException, httpx.TransportError,
                    httpx.HTTPStatusError) as exc:
                last = exc
                # 4xx（鉴权/参数错）重试没有意义
                if isinstance(exc, httpx.HTTPStatusError) and (
                    exc.response is not None and exc.response.status_code < 500
                ):
                    break
                if attempt < self.s.max_attempts:
                    backoff = 0.5 * (2 ** (attempt - 1))
                    logger.warning(
                        "检索失败（第 %d/%d 次），%.1fs 后重试：%s",
                        attempt, self.s.max_attempts, backoff, exc,
                    )
                    time.sleep(backoff)
        raise ResearchError(
            f"检索失败（{self.s.max_attempts} 次尝试）：query={query!r} — {last}"
        ) from last

    def research(self, topic: str) -> ResearchResult:
        """按主题做一次研究（单轮；多轮由编排层驱动，见 `research_plan`）。"""
        snippets = self.search(topic, self.s.max_results_per_query)
        return ResearchResult(
            topic=topic, snippets=tuple(snippets), stage="single_pass"
        )


class MockResearchProvider:
    """离线占位检索 —— 返回**带标记**的占位结果。

    ## 用途

    在没有 `TAVILY_API_KEY` 时把**全链路与可视化跑通**，
    以便在不花钱的前提下验证编排、缓存、缓存命中、UI 展示。

    ## ★ 结果必须可识别

    `title` 带 `[Mock]` 前缀、`url` 为空 —— 下游（知识点抽取）能据此
    拒绝把占位文本当真实知识来源。旧项目的 `MockSearchProvider`
    返回"（当前为 Mock 模式：未配置 TAVILY_API_KEY）"纯提示文本，
    **没有结构化标记**，容易被误当作检索结果。
    """

    def __init__(self, *, note: str = "") -> None:
        self.note = note or "未配置 TAVILY_API_KEY，返回占位结果"

    def search(self, query: str, max_results: int = 5) -> list[ResearchSnippet]:
        return [
            ResearchSnippet(
                title=f"[Mock] {query}（{self.note}）",
                url="",
                text=(
                    f"[Mock] 这是占位结果，不是真实检索内容。"
                    f"查询：{query}。若需真实知识点，请配置 TAVILY_API_KEY。"
                ),
            )
        ]

    def research(self, topic: str) -> ResearchResult:
        return ResearchResult(
            topic=topic,
            snippets=tuple(self.search(topic)),
            stage="mock",
        )


def make_research_provider(
    *, allow_mock: bool = True, settings: ResearchSettings | None = None
) -> TavilyResearchProvider | MockResearchProvider:
    """构造检索 provider —— **唯一工厂**。

    判据是**配置里有没有 key**，而不是"运行到某处发现没有 key 就降级"
    （后者是旧项目的形态，会静默改变行为）。

    `allow_mock=False` 时无 key 直接抛错 —— 供"必须真联网"的场景使用
    （例如最终验收）。

    ⚠️ 密钥经 `config.resolve_secret()` 读取，**不直读 `os.environ`**
    （契约规则 12：密钥解析必须集中在配置层，旧项目 7 个模块各自缓存的教训）。
    """
    s = settings or get_settings().research
    key = resolve_secret(s.api_key_env)
    if key:
        return TavilyResearchProvider(key, settings=s)
    if not allow_mock:
        raise ResearchError(
            f"未配置 {s.api_key_env}（且 allow_mock=False）—— "
            f"请在 .env 里设置该变量，或改用 MockResearchProvider"
        )
    logger.warning(
        "未配置 %s → 使用 MockResearchProvider（结果为占位文本，非真实检索）",
        s.api_key_env,
    )
    return MockResearchProvider()


__all__ = [
    "ResearchError",
    "TavilyResearchProvider",
    "MockResearchProvider",
    "make_research_provider",
]
