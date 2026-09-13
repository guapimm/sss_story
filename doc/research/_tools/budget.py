# -*- coding: utf-8 -*-
"""Final writer prompt budget breakdown with a token RANGE (no tiktoken available).

Token estimates for CJK-dominant text:
  low  = 0.60 tok / CJK char   (DeepSeek/cl100k-class BPE, Chinese-optimised)
  high = 1.00 tok / CJK char   (conservative upper bound, = 1 token per Han char)
  ASCII = 1 tok / 3.5 chars    (both ends)
"""
import os, sys, re

BACKEND = r"E:\AI_project\story_llm\backend"
sys.path.insert(0, BACKEND); os.chdir(BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(os.environ.get("TEMP", "."), "_probe4.db"))

CJK = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')

def toks(s):
    s = s or ""
    c = len(CJK.findall(s)); a = len(s) - c
    return c * 0.60 + a / 3.5, c * 1.00 + a / 3.5

def row(name, s, note=""):
    lo, hi = toks(s)
    print(f"| {name} | {len(s or ''):,} | {int(lo):,}–{int(hi):,} | {note} |")
    return len(s or "")

from app.agents.writer import _WRITE_BASE, WriterAgent, BEAT_INSTRUCTIONS
from app.flowmode import flow_for
from app import writer_rules
from app.templates.scenes import get_template

tpl = get_template("xianxia")
w = WriterAgent()
S = dict(bible=6000, timeline=3000, states=2200, events=1400, rag=4000, prev=800,
         skel=700, ent=1500, craft=400, detail=1500)

def ctx(**o):
    c = {"story_mode": True, "scene": "xianxia", "flow": "", "scale": "standard",
         "bible": "B"*S["bible"], "timeline": "T"*S["timeline"], "states": "S"*S["states"],
         "events": "E"*S["events"], "rag_snippets": "R"*S["rag"], "prev_prose": "P"*S["prev"],
         "skeleton_guide": "G"*S["skel"], "entity_registry": "N"*S["ent"],
         "story_craft": "C"*S["craft"], "style_snippets": ["X"*480, "Y"*470],
         "hang": {"open": True, "location": "镇公所", "activity": "talk", "hook": "李巡长逼问账本去向"},
         "volume_open": False, "graph_context": ""}
    c.update(o); return c

def ch(**o):
    c = {"idx": 12, "volume": 2, "title": "试探", "plot": "主角试探对手底细", "beat": "normal",
         "scale": "standard", "points": [], "plot_detail": "D"*S["detail"],
         "end_hook": "下一拍：李巡长亮出令牌", "prev_exit_state": "镇公所·夜·二人对坐",
         "next_entry_state": "主角离开镇公所", "plant_fs": ["FS3"], "resolve_fs": ["FS1", "FS2"]}
    c.update(o); return c

base = make = None
ctx0, ch0 = ctx(), ch()
full = w._build_prompt(ctx0, ch0, tpl)

# --- SYSTEM ---
sys_base = _WRITE_BASE.format(MIN_CHARS=3500, SOFT_MAX=6000, CLIMAX_MAX=7000)
sys_base = writer_rules.system_profile_transform(sys_base, ctx0, ch0)
sys_aa = writer_rules.anti_ai_block(ctx0, ch0)
sys_extra = flow_for(True).system_style(WriterAgent.LEARN_SYSTEM_EXTRA)
SYSTEM_TOTAL = sys_base + sys_aa + sys_extra
USER_TOTAL = full + writer_rules.output_requirement(ch0)

print("## WRITER PROMPT BUDGET (story mode / lean profile / standard scale)")
print()
print(f"SYSTEM total: {len(SYSTEM_TOTAL):,} chars   USER total: {len(USER_TOTAL):,} chars")
print(f"GRAND TOTAL : {len(SYSTEM_TOTAL)+len(USER_TOTAL):,} chars")
lo, hi = toks(SYSTEM_TOTAL + USER_TOTAL)
print(f"GRAND TOKENS: {int(lo):,} - {int(hi):,}")
print()
print("| block | chars | tokens | stable/volatile | source |")
print("|---|---|---|---|---|")
row("SYSTEM: _WRITE_BASE (24 hard rules)", sys_base, "STABLE (per scale)")
row("SYSTEM: anti_ai_block lean", sys_aa, "STABLE")
row("SYSTEM: learn extra (story='')", sys_extra, "STABLE")
row("USER: L0 世界观圣经", "B"*S["bible"], "STABLE-per-book")
row("USER: worldview_reminder", flow_for(True).worldview_reminder(), "STABLE")
row("USER: 防雷同提醒", "x"*170, "STABLE")
row("USER: 文风范本 wrapper+2 samples", "x"*(294+950), "VOLATILE")
row("USER: hang 现场承接", "x"*86, "VOLATILE")
row("USER: L2 时间线+防重演", "x"*3239, "VOLATILE")
row("USER: L5 上一章结尾", "x"*859, "VOLATILE")
row("USER: L3 人物状态表", "x"*2221, "VOLATILE")
row("USER: 名词表+引入约束", "x"*1693, "VOLATILE")
row("USER: L3 关键事件", "x"*1414, "VOLATILE")
row("USER: L4 前文片段", "x"*4014, "VOLATILE")
row("USER: 骨架指引", "x"*736, "VOLATILE")
row("USER: 伏笔任务", "x"*51, "VOLATILE")
row("USER: seasoning 文风指令", w._seasoning(ch0, ctx0), "VOLATILE")
row("USER: chapter_info+story_hard_rules+细纲+锚点", flow_for(True).chapter_info_block(ctx0, ch0), "VOLATILE")
row("USER: output_requirement", writer_rules.output_requirement(ch0), "STABLE")
row("USER: length_target_note", writer_rules.length_target_note(3500, 6000), "VOLATILE")
print()
print("--- learn mode contrast ---")
l_extra = flow_for(False).system_style(WriterAgent.LEARN_SYSTEM_EXTRA)
print(f"learn system extra = {len(l_extra)} chars (vs 0 in story)")
print(f"learn system total = {len(sys_base)+len(sys_aa)+len(l_extra):,} chars")
cib_l = flow_for(False).chapter_info_block(ctx0, ch0)
print(f"learn chapter_info_block = {len(cib_l)} chars (vs {len(flow_for(True).chapter_info_block(ctx0, ch0))} story)")
print()
print("--- story_hard_rules decomposition ---")
shr = writer_rules.story_hard_rules(ctx0, ch0)
print(f"story_hard_rules = {len(shr)} chars")
db = flow_for(True)._detail_block(ch0)
print(f"_detail_block     = {len(db)} chars")
print()
print("--- volatile vs stable split ---")
vol = sum(len(x) for x in ["x"*(170+294+950+86+3239+859+2221+1693+1414+4014+736+51),
                            w._seasoning(ch0, ctx0), flow_for(True).chapter_info_block(ctx0, ch0),
                            writer_rules.length_target_note(3500, 6000)])
print(f"volatile ≈ {vol:,} chars;  stable ≈ {len(SYSTEM_TOTAL)+len(USER_TOTAL)-vol:,} chars")
