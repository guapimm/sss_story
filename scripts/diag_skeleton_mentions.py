"""复算骨架 `new_locations` / `new_factions` 匹配 —— 判"真缺失 vs 措辞未命中"。

## 为什么需要这个脚本（D-16 的取证工具）

多书测试报出 `skeleton:new_locations` 时，有两种**性质完全不同**的可能：

  · **真缺失** —— 模型确实没把新地图写进故事线（该修 prompt）
  · **措辞未命中** —— 写了但校验器没认出来（该修匹配器）

两者处置相反，必须**先看数据再动手**。本文件把当轮骨架逐项复算，
把主名拆分、主名命中、LCS 长度全部打印出来，让判断有据可依。

## ⚠️ 必须用**当轮**产物

失败时 `skeleton/v{vol}.json` **不会被写入**（`persist_skeleton` 只在成功时写）。
去读它拿到的是**上一轮**的旧骨架 —— 用它复算会得到自相矛盾的结论，
并**凭空造出并不存在的缺陷**（实测踩到过，见 `doc/交接文档.md` §4.3）。

因此本脚本只读 `_diag_skeleton_{bid}.json`，并强制显示其 `skeleton_source`；
来源不是当轮时**拒绝给出结论**。

用法：
    uv run python scripts/diag_skeleton_mentions.py 16
    uv run python scripts/diag_skeleton_mentions.py 16 --vol 2
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys

sys.path.insert(0, "src")

from storyforge.graph.nodes.skeleton_planner import (  # noqa: E402
    _MENTION_MIN_LCS,
    _lcs_len,
    _mentions,
    _name_and_gloss,
    _normalize_for_match,
)

#: 可能出现在地名里的"地点字"（用于把骨架里提到的地方捞出来给人看）
_PLACE_TAIL = "市区局馆楼厂校院站所街路村镇"


def _load_diag(bid: int) -> dict:
    p = pathlib.Path(f"doc/research/_diag_skeleton_{bid}.json")
    if not p.exists():
        raise SystemExit(
            f"找不到 {p} —— 请先用诊断模式重跑：\n"
            f"  uv run python scripts/multibook_e2e.py --books {bid} --diag"
        )
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("book_id", type=int)
    ap.add_argument("--vol", type=int, default=1, help="卷号（默认 1）")
    args = ap.parse_args()

    diag = _load_diag(args.book_id)
    src = diag.get("skeleton_source", "?")
    print(f"骨架来源: {src}")
    if "当轮" not in src:
        print(
            "\n⚠️ 这不是**当轮**产物！\n"
            "   读旧产物复算会得出错误结论（实测踩到过）。\n"
            "   请用 --diag 重跑后再来。以下结果仅供参考，**不得据此下结论**。\n"
        )
    print()

    excerpt = diag.get("skeleton_excerpt") or ""
    try:
        sk = json.loads(excerpt)
    except json.JSONDecodeError:
        print("骨架原文无法解析为 JSON（可能是截断摘要）：")
        print(excerpt[:800])
        return 1

    arcs = sk.get("arcs", [])
    segs = sk.get("segments", [])
    loc_blob = (
        " ".join(f"{a.get('description', '')} {a.get('key', '')}" for a in arcs)
        + " "
        + " ".join(segs)
    )
    blob_n = _normalize_for_match(loc_blob)

    print("=== 骨架实际提到的地方（启发式捞取，供人快速核对）===")
    found = sorted(set(re.findall(
        rf"[\u4e00-\u9fa5]{{2,12}}(?:[{_PLACE_TAIL}])", blob_n
    )))
    for m in found or ["（没捞到像地名的串）"]:
        print("  ·", m)
    print()

    violations = diag.get("violations") or []
    if not violations:
        print("本卷没有违例（骨架已通过校验）。")
        return 0

    print("=== 逐项复算 ===")
    for viol in violations:
        if "未提及本卷新地图" not in viol and "未落实本卷台阶指定的新势力" not in viol:
            print(f"[其它违例] {viol}")
            continue
        kind = "新地图" if "新地图" in viol else "新势力"
        tail = viol.partition("：")[2].split(" —— ")[0]
        try:
            items = ast.literal_eval(tail)
        except (ValueError, SyntaxError):
            print(f"[{kind}] 无法解析违例列表：{tail!r}")
            continue
        for item in items:
            name, gloss = _name_and_gloss(item)
            qn = _normalize_for_match(name)
            lcs = _lcs_len(blob_n, _normalize_for_match(item))
            hit = bool(qn) and qn in blob_n
            verdict = "落实" if _mentions(loc_blob, item) else "**未落实**"
            print(f"[{kind}] {item!r}")
            print(f"    主名 = {name!r}   括注 = {gloss!r}")
            print(f"    主名命中 = {hit}   整串 LCS = {lcs} (阈值 {_MENTION_MIN_LCS})")
            print(f"    → {verdict}")
            print()

    print("=== 骨架 factions（对照用）===")
    for f in sk.get("factions", []):
        print("  ·", f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
