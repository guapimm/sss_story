"""变异测试：证明"实体入库"这条链路**真的被测试钉住**了。

## 为什么这个变异测试特别重要

本轮修的缺口是「**表建好了、仓储写好了、没人用**」型缺陷 ——
它**不报错、不崩溃、单测可能全绿**（修复前 950 passed 就是绿的）。
所以"加了几条测试"不足以说明问题；必须证明：
**把接线删掉，测试会红。** 否则新增的测试只是装饰。

## 四个变异（每个都忠实复现一种"真实的退化方式"）

  变异 1：`commit` 节点不再调用 `persist_chapter_memory`
          → 复现**原始缺口**（抽取跑了但库里没有）
  变异 2：`persist_extraction` 里不落关系（只落实体）
          → 复现"实体有了但关系网为空"（部分接线）
  变异 3：`get_or_create` 改成每次 insert（不做别名/规范名归一）
          → 复现"同一实体两个名字建成两行"
  变异 4：`upsert` 退化为"已存在则跳过"
          → 复现"强度/暗线永不更新"（旧项目旧边不解、新边累积的镜像）

★ 变异必须**忠实复现真实改动**，否则证明的是假的东西
（本项目已记录该教训：`mutate_extract_shapes.py` 初版变异写错了方向）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHAPTER_NODES = ROOT / "src/storyforge/graph/graphs/chapter_nodes.py"
PERSIST = ROOT / "src/storyforge/graph/nodes/persist.py"
ENTITIES = ROOT / "src/storyforge/infra/storage/repositories/entities.py"
RELATIONS = ROOT / "src/storyforge/infra/storage/repositories/relations.py"

TEST_ID = "tests/unit/test_persist_extraction.py"


def _run() -> tuple[bool, str]:
    r = subprocess.run(
        [sys.executable, "-m", "pytest", TEST_ID, "-q"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT,
    )
    return r.returncode != 0, (r.stdout or "")[-700:]


def _case(title: str, path: Path, mutate) -> bool:
    original = path.read_text(encoding="utf-8")
    mutated = mutate(original)
    assert mutated != original, f"变异「{title}」没有改动文件"
    path.write_text(mutated, encoding="utf-8")
    caught, out = _run()
    path.write_text(original, encoding="utf-8")     # 立即还原
    assert path.read_text(encoding="utf-8") == original, "还原失败！"

    print(f"  {'✓ 拦下' if caught else '★ 放行（测试无效）'}  {title}")
    if caught:
        for line in out.splitlines():
            if "assert" in line or "Error" in line:
                print(f"        {line.strip()[:100]}")
                break
    return caught


def main() -> int:
    results: list[bool] = []

    print("=" * 76)
    print("变异 1：commit 节点不再落库（复现**原始缺口**）")
    print("=" * 76)
    results.append(_case(
        "commit 节点不调用 persist_chapter_memory",
        CHAPTER_NODES,
        lambda s: s.replace(
            "        note = persist_chapter_memory(\n"
            "            deps, book_id=book_id, global_no=global_no,\n"
            "            extraction_ref=str(state[\"extraction_ref\"]), out=out,\n"
            "        )\n"
            "        out[\"commit_note\"] = note\n",
            "        out[\"commit_note\"] = \"（变异：跳过落库）\"\n",
            1,
        ),
    ))

    print()
    print("=" * 76)
    print("变异 2：只落实体、不落关系（部分接线）")
    print("=" * 76)
    results.append(_case(
        "关系不落库",
        PERSIST,
        lambda s: s.replace(
            "        relation_repo.upsert(",
            "        _skip = True\n"
            "        if _skip:\n"
            "            continue\n"
            "        relation_repo.upsert(",
            1,
        ),
    ))

    print()
    print("=" * 76)
    print("变异 3：get_or_create 不再做规范名归一（同一实体多行）")
    print("=" * 76)
    results.append(_case(
        "规范名归一失效",
        ENTITIES,
        lambda s: s.replace(
            "        existing = self.find_by_name(book_id, clean, kind=ek)",
            "        existing = None   # 变异：永不复用既有实体",
            1,
        ),
    ))

    print()
    print("=" * 76)
    print("变异 4：upsert 退化为「已存在则跳过」（强度永不更新）")
    print("=" * 76)
    results.append(_case(
        "关系演化失效",
        RELATIONS,
        lambda s: s.replace(
            "            else:\n"
            "                # ★ 演化：只更新会变的字段，`since_chapter` 保留首次值\n"
            "                row.intensity = int(intensity)",
            "            else:\n"
            "                sess.flush()\n"
            "                return self.to_domain(row)   # 变异：已存在就跳过更新\n"
            "                row.intensity = int(intensity)",
            1,
        ),
    ))

    print()
    print("=" * 76)
    print("还原后复验")
    print("=" * 76)
    ok, _ = _run()
    print(f"  {'★ 仍在失败（还原失败）' if ok else '全部测试通过 ✓'}")

    print()
    print("=" * 76)
    passed = sum(results)
    all_ok = passed == len(results) and not ok
    print(f"  结论：{passed}/{len(results)} 个变异被拦下"
          f"{'，且还原后通过 ✓' if all_ok else '　★ 不通过'}")
    print("=" * 76)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
