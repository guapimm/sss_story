"""抽取旧项目 story_genres.py 的题材与流派清单 —— 这是「已有的」权威来源。

用户要求「玄幻修仙的流派清单，根据已有的来」，所以不自己造枚举，
而是从旧项目 `story_genres.py`（251 行）里提取：
  · 题材（GENRES）的 id / name / channel / tags / flows
  · 流派（flows）的**全集** —— 各题材的 flows 并集去重
"""

from __future__ import annotations

import ast
import pathlib
import sys

SRC = pathlib.Path(r"E:\AI_project\story_llm\backend\app\story_genres.py")
text = SRC.read_text(encoding="utf-8")
tree = ast.parse(text)

# ── 用 AST 取出 GENRES 字面量（不 import 旧项目，避免拖入其依赖）──
genres_node = None
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "GENRES":
                genres_node = node.value
if genres_node is None:
    raise SystemExit("找不到 GENRES")

# GENRES 是 _g(...) 调用的列表 → 取每个调用的字面量参数
CHANNELS: dict[str, str] = {}
rows: list[dict] = []
flows_union: dict[str, set[str]] = {}

for elt in genres_node.elts:
    if not isinstance(elt, ast.Call) or not elt.args:
        continue
    vals = []
    for a in elt.args:
        try:
            vals.append(ast.literal_eval(a))
        except Exception:  # noqa: BLE001
            vals.append(None)
    if len(vals) < 11:
        continue
    gid, channel, name, tags, _world, _ladder, _naming, _tropes, _expect, _tones, flows = vals[:11]
    rows.append({
        "id": gid, "channel": channel, "name": name,
        "tags": tags or [], "flows": flows or [],
    })
    for f in (flows or []):
        flows_union.setdefault(f, set()).add(gid)

print("=" * 88)
print(f"旧项目 story_genres.py 的题材清单（{len(rows)} 个）")
print("=" * 88)
print(f"{'id':<14}{'频道':<8}{'名称':<12}{'流派数':<8}tags")
for r in rows:
    print(f"{r['id']:<14}{r['channel']:<8}{r['name']:<12}"
          f"{len(r['flows']):<8}{'、'.join(r['tags'][:5])}")

print()
print("=" * 88)
print(f"★ 流派（flows）全集 —— 共 {len(flows_union)} 个")
print("=" * 88)
print(f"{'流派 id':<18}{'出现于题材':<10}题材")
for fid, gids in sorted(flows_union.items(), key=lambda kv: -len(kv[1])):
    print(f"{fid:<18}{len(gids):<10}{'、'.join(sorted(gids))}")

print()
print("=" * 88)
print("★ 修仙玄幻（xianxia）自己的 flows（用户要的清单）")
print("=" * 88)
xian = next((r for r in rows if r["id"] == "xianxia"), None)
if xian:
    for f in xian["flows"]:
        owners = sorted(flows_union.get(f, set()))
        print(f"  {f:<16}（也用于：{'、'.join(owners)}）")

print()
print("=" * 88)
print("流派 id 的含义（从各题材的 tags/名称推断，供人工确认）")
print("=" * 88)
GUESS = {
    "rebirth": "重生流（带前世记忆重来）",
    "system": "系统流（任务面板/金手指系统）",
    "underdog": "废柴逆袭流",
    "invincible": "无敌流（开局即强）",
    "sect": "宗门流（门派成长）",
    "immortal_road": "仙路流（求道飞升）",
    "face_slap": "打脸流（身份揭露反击）",
    "business": "商战流",
    "infinite": "无限流（副本/轮回）",
    "tech_tycoon": "科技霸主流",
    "base_build": "基地建设流",
    "detective": "推理探案流",
    "mystery": "悬疑解谜流",
}
for fid in sorted(flows_union):
    print(f"  {fid:<16}{GUESS.get(fid, '★ 待人工确认')}")
