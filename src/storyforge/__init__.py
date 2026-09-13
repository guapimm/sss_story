"""storyforge —— 基于 LangChain + LangGraph 的长篇小说生成系统。

架构分层（依赖严格单向，见 doc/01-总体架构.md）：

    L5 接口层    cli / api
    L4 编排层    orchestrator（三级流水线协调器，纯 Python）
    L3 图与节点  graph/（LangGraph 工作单元 + LLM 节点）
    L2 领域层    domain/ scorecard/   ← 纯逻辑，零 LLM 零 SQL 零框架
    L1 基础设施  infra/（LLM 客户端 / Prompt / 仓储 / 检索）
    L0 存储      SQLite + 文件 + checkpoint DB

硬约束（由 tests/contract 强制）：
  · domain/ 与 scorecard/ 不得 import langgraph / langchain
  · domain/ 与 graph/nodes/ 不得出现模式分支（book_kind / is_story）
  · 任何模块不得硬编码 model 名
"""

__version__ = "0.1.0"
