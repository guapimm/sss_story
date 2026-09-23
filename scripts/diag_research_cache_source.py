"""诊断：研究缓存里到底是**真实检索**还是 Mock 占位？

## 为什么必须先确认这件事

`_gen_with_llm` 的输入是研究缓存里的片段。若缓存里是 Mock 占位文本
（`[Mock] …` + 空 URL），那么用它来验证 LLM 路径：
  · 能验证**代码通路**（会不会 NameError / 形状漂移 / 空输出）
  · **不能**验证**知识点质量**（输入本身就是假的）
⇒ 结论必须分清"路径通了"与"产出可用"。

★ 本项目踩过同类坑（交接文档 §5.1「看到的失败与真实的失败错位」），
故先取证，不凭印象。

## 用法

    uv run python scripts/diag_research_cache_source.py            # 只诊断
    uv run python scripts/diag_research_cache_source.py --clean    # 诊断 + 删掉 Mock 条目

`--clean` 的判据：**只要一个 key 里含 Mock 片段就整个删掉** ——
"部分 Mock"同样不可信（真实与占位混在一起，无法判断某条知识点来自哪边）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CACHE = Path(__file__).resolve().parent.parent / "data" / "research_cache.json"


def _mock_stats(entry: object) -> tuple[int, int]:
    """返回 (片段总数, Mock 条数)。形状不符时返回 (0, 0)。"""
    if not isinstance(entry, dict):
        return 0, 0
    result = entry.get("result") or {}
    if not isinstance(result, dict):
        return 0, 0
    sns = result.get("snippets") or []
    if not isinstance(sns, list):
        return 0, 0
    sns = [s for s in sns if isinstance(s, dict)]
    mock = sum(1 for s in sns if str(s.get("title", "")).startswith("[Mock]"))
    return len(sns), mock


def _walk_entries(node, depth: int = 0) -> None:
    """缓存结构未知，故按形状自适应下钻（先取证，不假设）。"""
    if isinstance(node, dict):
        # 看起来像一条研究结果
        if "result" in node or "snippets" in node:
            res = node.get("result") or node
            sns = res.get("snippets") or []
            if isinstance(sns, list) and sns:
                titles = [str(s.get("title", ""))[:40] for s in sns if isinstance(s, dict)]
                urls = [s.get("url") for s in sns if isinstance(s, dict) and s.get("url")]
                mock = [t for t in titles if t.startswith("[Mock]")]
                print(f"  {'─' * 66}")
                print(f"  片段 {len(sns)} 条 | 有 URL {len(urls)} 条 | [Mock] {len(mock)} 条")
                for t in titles[:3]:
                    print(f"     title: {t!r}")
                for u in urls[:2]:
                    print(f"     url  : {u}")
                if not urls:
                    print("     ★ 全部无 URL —— 这是 Mock 占位（或检索未回传链接）")
                return
        for k, v in node.items():
            print(f"  {'  ' * depth}[key] {k}")
            _walk_entries(v, depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node[:4]):
            print(f"  {'  ' * depth}[{i}]")
            _walk_entries(v, depth + 1)
    else:
        print(f"  {'  ' * depth}{type(node).__name__}: {str(node)[:60]}")


def main() -> int:
    clean = "--clean" in sys.argv

    print("=" * 72)
    print(f"研究缓存：{CACHE}")
    print("=" * 72)
    if not CACHE.exists():
        print("★ 缓存文件不存在")
        return 1
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    print(f"顶层类型：{type(data).__name__}，键 {list(data)[:8] if isinstance(data, dict) else '—'}")
    print()
    _walk_entries(data)

    # 逐 key 判定（决定删哪些）------------------------------------------------
    to_drop: list[str] = []
    if isinstance(data, dict):
        print()
        print("=" * 72)
        print(f"{'key':<28}{'片段':>6}{'Mock':>6}  判定")
        print("=" * 72)
        for key, entry in data.items():
            total, mock = _mock_stats(entry)
            if mock:
                to_drop.append(key)
                verdict = "含 Mock → 应删"
            elif total:
                verdict = "真实数据 → 保留"
            else:
                verdict = "空条目 → 保留（无害）"
            print(f"{str(key):<28}{total:>6}{mock:>6}  {verdict}")

    print()
    print("=" * 72)
    raw = CACHE.read_text(encoding="utf-8")
    n_mock = raw.count("[Mock]")
    print(f"整个文件里 '[Mock]' 出现 {n_mock} 次 → "
          f"{'★ 是 Mock 数据（输入非真实）' if n_mock else '是真实检索数据 ✓'}")
    print("=" * 72)

    if not clean:
        if to_drop:
            print()
            print(f"提示：{len(to_drop)} 个 key 含 Mock，可加 --clean 删除：")
            for key in to_drop:
                print(f"    - {key}")
        return 0

    # 清理 -------------------------------------------------------------------
    if not to_drop:
        print("无需清理。")
        return 0
    for key in to_drop:
        del data[key]
    CACHE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print()
    print(f"★ 已删除 {len(to_drop)} 个含 Mock 的条目：")
    for key in to_drop:
        print(f"    - {key}")
    print(f"剩余 {len(data)} 个：{list(data) or '（空）'}")
    print()
    print("建议重跑一次真实研究，确认新缓存干净：")
    print('    uv run storyforge research -t "宋代点茶" -s standard --no-cache')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
