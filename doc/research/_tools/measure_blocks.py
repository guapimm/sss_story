# -*- coding: utf-8 -*-
"""Per-block breakdown of WriterAgent._build_prompt (story mode, lean profile)."""
import os, sys, json, re

BACKEND = r"E:\AI_project\story_llm\backend"
sys.path.insert(0, BACKEND); os.chdir(BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(os.environ.get("TEMP", "."), "_probe2.db"))

CJK = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')
def toks(s):
    s = s or ""; c = len(CJK.findall(s)); return int(c + (len(s) - c) / 3.5)

from app.agents.writer import WriterAgent
from app.templates.scenes import get_template

w = WriterAgent()
tpl = get_template("xianxia")

# Realistic per-block sizes sampled from actual production caps (memory.py / pipeline.py)
SIZES = {
    "bible": 6000,        # docstore bible ~18-20KB; genre_state appended; typical injected length
    "timeline": 3000,
    "states": 2200,
    "events": 1400,
    "rag_snippets": 4000,
    "prev_prose": 800,    # read_prev_chapter_tails cap
    "skeleton_guide": 700,
    "entity_registry": 1500,
    "graph_context": 0,   # default off
    "story_craft": 400,
}

def make_ctx(**over):
    c = {
        "story_mode": True, "scene": "xianxia", "flow": "", "scale": "standard",
        "bible": "B" * SIZES["bible"],
        "timeline": "T" * SIZES["timeline"],
        "states": "S" * SIZES["states"],
        "events": "E" * SIZES["events"],
        "rag_snippets": "R" * SIZES["rag_snippets"],
        "prev_prose": "P" * SIZES["prev_prose"],
        "hang": {"open": True, "location": "镇公所", "activity": "talk", "hook": "李巡长逼问账本去向"},
        "volume_open": False,
        "skeleton_guide": "G" * SIZES["skeleton_guide"],
        "entity_registry": "N" * SIZES["entity_registry"],
        "graph_context": "",
        "style_snippets": ["X" * 480, "Y" * 470],
        "story_craft": "C" * SIZES["story_craft"],
    }
    c.update(over); return c

def make_ch(**over):
    ch = {"idx": 12, "volume": 2, "title": "试探", "plot": "主角试探对手底细",
          "beat": "normal", "scale": "standard", "points": [],
          "plot_detail": "D" * 1500, "end_hook": "下一拍：李巡长亮出令牌",
          "prev_exit_state": "镇公所·夜·二人对坐", "next_entry_state": "主角离开镇公所",
          "plant_fs": ["FS3"], "resolve_fs": ["FS1", "FS2"]}
    ch.update(over); return ch

ctx = make_ctx(); ch = make_ch()
full = w._build_prompt(ctx, ch, tpl)
print(f"{'FULL _build_prompt':46s} {len(full):7d}  ~{toks(full):6d}")

# measure each block by rendering with that key removed and diffing
drops = [
    ("L0 世界观圣经 block", dict(bible="")),
    ("worldview_reminder (story)", None),   # measured separately below
    ("防雷同提醒 (hardcoded)", None),
    ("flow_card", dict(flow_card=None)),
    ("文风范本 style_snippets+警告", dict(style_snippets=[])),
    ("term_mapping_section (story='')", None),
    ("本卷开篇/现场必须承接 hang", dict(hang=None, volume_open=False)),
    ("L2 前情时间线 + 防重演硬规则", dict(timeline="")),
    ("L5 上一章结尾原文", dict(prev_prose="")),
    ("L3 人物状态表", dict(states="")),
    ("图谱关系快照", dict(graph_context="")),
    ("已注册名词表 + 引入硬约束", dict(entity_registry="")),
    ("断点补写桥接 bridge", dict(bridge_prev="", bridge_next="")),
    ("L3 关键事件记忆", dict(events="")),
    ("L4 相关前文片段", dict(rag_snippets="")),
    ("骨架指引 skeleton_guide", dict(skeleton_guide="")),
    ("伏笔任务 plant/resolve", dict(_fs=True)),
    ("复习候选 due_reviews", dict(_due=True)),
    ("本章文风指令 _seasoning", None),
    ("beat 指令 BEAT_INSTRUCTIONS/boss", None),
    ("全书终章收束指令", dict(_finale=True)),
    ("chapter_info_block 本章信息+细纲+故事导演备忘", None),
    ("length_target_note", None),
]

print("-" * 78)
rows = []
for name, over in drops:
    if over is None:
        continue
    c2 = make_ctx(); c2.update(over); ch2 = make_ch()
    if over.get("_fs"):
        c2 = make_ctx(); ch2 = make_ch(plant_fs=[], resolve_fs=[])
    if over.get("_due"):
        c2 = make_ctx(); ch2 = make_ch(due_reviews=[])
    if over.get("_finale"):
        c2 = make_ctx(); ch2 = make_ch(is_finale=True)
    v = w._build_prompt(c2, ch2, tpl)
    d = len(full) - len(v)
    if over.get("_finale"):
        d = len(v) - len(full)
    rows.append((name, d))
    print(f"{name:46s} {d:7d}  ~{toks('x'*max(d,0)):6d}")

# direct measures
from app.flowmode import flow_for
from app import writer_rules
print("-" * 78)
direct = {
  "worldview_reminder (story)": flow_for(True).worldview_reminder(),
  "term_mapping_section (story)": flow_for(True).term_mapping_section(["a"]),
  "防雷同提醒 (hardcoded)": [x for x in [None]],
}
for k, v in direct.items():
    if isinstance(v, str):
        print(f"{k:46s} {len(v):7d}  ~{toks(v):6d}")

# chapter_info_block for story
cib = flow_for(True).chapter_info_block(make_ctx(), make_ch())
print(f"{'chapter_info_block TOTAL (story)':46s} {len(cib):7d}  ~{toks(cib):6d}")
_saved = flow_for(True).__class__.chapter_info_block
class NoCraft(type(flow_for(True))):
    def __init__(self): pass
# story_hard_rules included
shr = writer_rules.story_hard_rules(ctx, ch)
print(f"{'  └ story_hard_rules (inside)':46s} {len(shr):7d}  ~{toks(shr):6d}")
db = flow_for(True)._detail_block(make_ch())
print(f"{'  └ _detail_block (细纲/钩子/前后锚点)':46s} {len(db):7d}  ~{toks(db):6d}")

season = w._seasoning(make_ch(), make_ctx())
print(f"{'seasoning 文风指令':46s} {len(season):7d}  ~{toks(season):6d}")
print(f"{'output_requirement':46s} {len(writer_rules.output_requirement(make_ch())):7d}  ~{toks(writer_rules.output_requirement(make_ch())):6d}")
print(f"{'length_target_note':46s} {len(writer_rules.length_target_note(3500,6000)):7d}  ~{toks(writer_rules.length_target_note(3500,6000)):6d}")

# system
from app.agents.writer import _WRITE_BASE, WriterAgent as WA
base = _WRITE_BASE.format(MIN_CHARS=3500, SOFT_MAX=6000, CLIMAX_MAX=7000)
print("-" * 78)
print(f"{'SYSTEM _WRITE_BASE (formatted)':46s} {len(base):7d}  ~{toks(base):6d}")
aa = writer_rules.anti_ai_block(ctx, ch)
print(f"{'SYSTEM anti_ai_block (lean)':46s} {len(aa):7d}  ~{toks(aa):6d}")
print(f"{'SYSTEM learn extra (LEARN_SYSTEM_EXTRA)':46s} {len(WA.LEARN_SYSTEM_EXTRA):7d}  ~{toks(WA.LEARN_SYSTEM_EXTRA):6d}")
print(f"{'legacy anti-ai (for contrast)':46s} {len(writer_rules.LEGACY_ANTI_AI_RULES):7d}  ~{toks(writer_rules.LEGACY_ANTI_AI_RULES):6d}")

# learn-mode system total
c3 = make_ctx(story_mode=False, scene="xianxia")
base_l = _WRITE_BASE.format(MIN_CHARS=3500, SOFT_MAX=6000, CLIMAX_MAX=7000)
aa_l = writer_rules.anti_ai_block(c3, ch)
extra_l = flow_for(False).system_style(WA.LEARN_SYSTEM_EXTRA)
print(f"{'LEARN SYSTEM TOTAL':46s} {len(base_l)+len(aa_l)+len(extra_l):7d}  ~{toks('x'*(len(base_l)+len(aa_l)+len(extra_l))):6d}")
print(f"{'  └ learn base+anti_ai':46s} {len(base_l)+len(aa_l):7d}")
print(f"{'  └ learn LEARN_SYSTEM_EXTRA':46s} {len(extra_l):7d}")
