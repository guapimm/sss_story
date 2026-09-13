# -*- coding: utf-8 -*-
"""Measure the REAL writer prompt block sizes by calling production code read-only.

No files are written under story_llm; a temp sqlite DB is created in %TEMP%.
"""
import os, sys, json, io

BACKEND = r"E:\AI_project\story_llm\backend"
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(os.environ.get("TEMP", "."), "_probe_measure.db"))

CJK = __import__("re").compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')
def toks(s):
    s = s or ""
    c = len(CJK.findall(s))
    return int(c + (len(s) - c) / 3.5)

def L(name, s):
    s = s or ""
    print(f"{name:44s} {len(s):7d} chars  ~{toks(s):6d} tok")
    return len(s)

out = {}
from app import writer_rules
print("rule_profile =", writer_rules.rule_profile())
print("=" * 78)

# --- style pools ---
for pool in ("OPENING_STYLES", "FOCUS_STYLES", "MOOD_STYLES"):
    p = getattr(writer_rules, pool, []) or []
    print(f"{pool}: {len(p)} entries, lens={[len(str(x)) for x in p]}")
print("=" * 78)

# --- writer system prompt, story mode, standard scale ---
chapter = {"idx": 12, "volume": 1, "title": "试探", "plot": "主角试探对手底细",
           "beat": "normal", "scale": "standard", "points": []}
context = {"story_mode": True, "scene": "xianxia", "flow": "", "scale": "standard"}

from app.agents.writer import _WRITE_BASE, WriterAgent, BEAT_INSTRUCTIONS
from app.flowmode import flow_for

print("--- WRITER SYSTEM (story mode) ---")
base = _WRITE_BASE.format(MIN_CHARS=3500, SOFT_MAX=6000, CLIMAX_MAX=7000)
out["sys._WRITE_BASE(formatted)"] = L("_WRITE_BASE formatted", base)
t = writer_rules.system_profile_transform(base, context, chapter)
out["sys.system_profile_transform(delta)"] = L("system_profile_transform DELTA", t[len(base):] if len(t) > len(base) else "")
a = writer_rules.anti_ai_block(context, chapter)
out["sys.anti_ai_block"] = L("anti_ai_block", a)
s = flow_for(True).system_style(WriterAgent.LEARN_SYSTEM_EXTRA)
out["sys.flow system_style"] = L("flow.system_style (story='')", s)
total_sys = len(t) + len(a) + len(s)
out["sys.TOTAL"] = L("SYSTEM TOTAL", "x" * total_sys)
print("=" * 78)

# --- writer USER prompt blocks (story mode) ---
print("--- WRITER USER (story mode) ---")
w = WriterAgent()
class FakeBible: pass
from app.templates.scenes import get_template
tpl = get_template("xianxia")
print("scene template keys:", list(tpl.keys()))
print("style_prompt len:", len(tpl.get("style_prompt") or ""))
print("mapping_rules:", len(tpl.get("mapping_rules") or []), [len(x) for x in (tpl.get("mapping_rules") or [])])
print("style_samples:", len(tpl.get("style_samples") or []), [len(x) for x in (tpl.get("style_samples") or [])])
print("=" * 78)

# measure each block of _build_prompt by monkeypatching a fake context with sentinel sizes
blocks = {}
bible = "B" * 6000
ctx = {
    "story_mode": True, "scene": "xianxia", "flow": "", "scale": "standard",
    "bible": bible, "timeline": "T" * 3000, "states": "S" * 2000,
    "events": "E" * 1500, "rag_snippets": "R" * 4000,
    "prev_prose": "P" * 800, "hang": {"open": True, "location": "镇公所", "activity": "talk", "hook": "李巡长逼问"},
    "volume_open": False, "skeleton_guide": "G" * 800,
    "entity_registry": "N" * 1200, "graph_context": "",
    "style_snippets": ["X" * 500, "Y" * 500],
    "story_craft": "C" * 400,
}
ch = dict(chapter)
ch["plot_detail"] = "D" * 1500
ch["end_hook"] = "下一拍：李巡长亮出令牌"
ch["prev_exit_state"] = "镇公所·夜·二人对坐"
ch["next_entry_state"] = "主角离开镇公所"
u = w._build_prompt(ctx, ch, tpl)
out["user.TOTAL(_build_prompt, sized ctx)"] = L("USER _build_prompt TOTAL", u)
u += flow_for(True).worldview_reminder() and ""
u2 = u + writer_rules.output_requirement(ch)
out["user.output_requirement"] = L("output_requirement", writer_rules.output_requirement(ch))
u3 = u2 + writer_rules.length_target_note(3500, 6000)
out["user.length_target_note"] = L("length_target_note", writer_rules.length_target_note(3500, 6000))
out["user.FINAL TOTAL"] = L("USER FINAL TOTAL", u3)
print("=" * 78)
print("--- rule function coverage ---")
for fn in ("story_hard_rules", "anti_ai_block", "output_requirement", "length_target_note",
           "continuation_requirement", "length_retry_note", "opening_focus_mood", "chapter_seed"):
    try:
        f = getattr(writer_rules, fn)
        print(f"{fn}: OK {f}")
    except Exception as e:
        print(f"{fn}: MISSING {e}")
try:
    L("story_hard_rules(ctx,ch)", writer_rules.story_hard_rules(ctx, ch))
except Exception as e:
    print("story_hard_rules err", e)
print("=" * 78)
json.dump(out, io.open(os.path.join(os.environ["TEMP"], "_probe_out.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("saved ->", os.path.join(os.environ["TEMP"], "_probe_out.json"))
