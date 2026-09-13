# -*- coding: utf-8 -*-
"""Measure Editor / PostProcessor / other agent prompt sizes as actually rendered."""
import os, sys, re, json

BACKEND = r"E:\AI_project\story_llm\backend"
sys.path.insert(0, BACKEND); os.chdir(BACKEND)
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(os.environ.get("TEMP", "."), "_probe3.db"))

CJK = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')
def tok(s):
    s = s or ""; c = len(CJK.findall(s)); return int(c + (len(s) - c) / 3.5)
def L(n, s):
    s = s or ""
    print(f"{n:50s} {len(s):7d} chars  ~{tok(s):6d} tok")

from app.agents.editor import EditorAgent
from app.agents.post_processor import ChapterPostProcessor
from app.agents.audit import VolumeAuditor
from app.agents.gate import GateAgent
from app.agents.era_checker import EraCheckerAgent
from app.agents.worldbuilder import WorldBuilderAgent
from app.agents.story_director import StoryDirectorAgent
from app.agents.story_arc_planner import StoryArcPlannerAgent
from app.agents.verifier import VerifierAgent
from app.agents.researcher import QueryPlannerAgent, ResearchAgent, VerifyAgent, SatelliteExpanderAgent
from app.agents.architect import ArchitectAgent, SYSTEM_TEXT as ARCH_SYS, foreshadow_prompt_block
from app.agents.bible_reviewer import REVIEW_SYSTEM

print("=" * 84)
print("SYSTEM PROMPTS (as-defined, ctx=story, scene=xianxia)")
print("=" * 84)
ctx = {"story_mode": True, "scene": "xianxia", "flow": ""}
e = EditorAgent()
L("EditorAgent.system (raw)", EditorAgent.system)
L("EditorAgent LEARN_SYSTEM_EXTRA", EditorAgent.LEARN_SYSTEM_EXTRA)
L("Editor._build_system(story)", e._build_system(ctx))
L("Editor._build_system(learn)", e._build_system({"story_mode": False, "scene": "xianxia", "flow": ""}))
L("PostProcessor.system (learn)", ChapterPostProcessor.system)
L("PostProcessor.STORY_SYSTEM", ChapterPostProcessor.STORY_SYSTEM)
L("VolumeAuditor.system", VolumeAuditor.system)
L("GateAgent.system", GateAgent.system)
L("EraCheckerAgent.system", EraCheckerAgent.system)
L("WorldBuilder.BIBLE_SYSTEM", WorldBuilderAgent.BIBLE_SYSTEM)
L("WorldBuilder.BIBLE_SYSTEM_STORY", WorldBuilderAgent.BIBLE_SYSTEM_STORY)
L("WorldBuilder.REALM_SYSTEM_EXTRA", WorldBuilderAgent.REALM_SYSTEM_EXTRA)
L("WorldBuilder.CHARACTERS_SYSTEM", WorldBuilderAgent.CHARACTERS_SYSTEM)
L("StoryDirector.SYSTEM_TEXT", __import__("app.agents.story_director", fromlist=["x"]).SYSTEM_TEXT)
L("StoryArcPlanner.SYSTEM_TEXT", __import__("app.agents.story_arc_planner", fromlist=["x"]).SYSTEM_TEXT)
L("Architect.SYSTEM_TEXT", ARCH_SYS)
L("BibleReviewer.REVIEW_SYSTEM", REVIEW_SYSTEM)
L("Verifier.system", VerifierAgent.system)
L("Verifier.SYSTEM_ENTITIES", VerifierAgent.SYSTEM_ENTITIES)
L("Verifier.SYSTEM_DRIFT", VerifierAgent.SYSTEM_DRIFT)
L("Verifier.SYSTEM_LOGIC", VerifierAgent.SYSTEM_LOGIC)
L("QueryPlanner.system", QueryPlannerAgent.system)
L("ResearchAgent.system", ResearchAgent.system)
L("VerifyAgent.system", VerifyAgent.system)
L("SatelliteExpander.GEN_SYSTEM", SatelliteExpanderAgent.GEN_SYSTEM)
print("=" * 84)

# Architect story system (STORY_SYSTEM_TEXT)
import app.agents.architect as A
src = open(os.path.join(BACKEND, "app/agents/architect.py"), encoding="utf-8").read()
m = re.search(r'STORY_SYSTEM_TEXT\s*=\s*"""(.*?)"""', src, re.S)
if m:
    L("Architect.STORY_SYSTEM_TEXT", m.group(1))
m2 = re.search(r'_BLUEPRINT_BLOCK\s*=\s*\((.*?)\n    \)', src, re.S)
print("=" * 84)
print("ARCHITECT USER-PROMPT INJECTED BLOCKS")
L("foreshadow_prompt_block(standard,50)", foreshadow_prompt_block("standard", 50))
L("foreshadow_prompt_block(ultra,400)", foreshadow_prompt_block("ultra", 400))
L("_protagonist_anchor()", ArchitectAgent().__dict__ and ArchitectAgent()._protagonist_anchor() or "n/a")
L("TRUNCATE_RETRY", A.TRUNCATE_RETRY)
L("fill_segment_details _fill_ctx", "为已锁定的章节骨架补写剧情（第1卷）。每章输出 plot≤100、end_hook≤40、plot_detail≤2000（分场景拍点表）；plot 承接上一章并停在悬而未决处；end_hook 只写本章现场内悬而未决的具体状态，禁止预兆式预警/无主暗示。")
print("=" * 84)

# Editor user prompt, realistically sized
ed_user = (
    f"【世界观圣经(节选)】\n{'B'*1800}\n\n"
    f"【人物状态表】\n{'S'*2500}\n\n"
    f"【前情时间线】\n{'T'*1500}\n\n"
    f"【本书伏笔任务】埋:[] 收:[]\n\n"
    f"【本章规划·章末钩子】end_hook=...\n\n"
    f"【本章规划·细纲】plot\n{'D'*1500}\n\n"
    f"【纯小说审校】不要检查知识卡或教学准确性；重点查人物欲望、因果、伏笔、姓名与文风。\n\n"
    f"【章节草稿】\n{'C'*4600}\n\n请审查。"
)
L("Editor user (typical, 4600-char draft)", ed_user)
L("Editor TOTAL (system+user, story)", f"{'x'*(len(e._build_system(ctx)))}" + ed_user)
print("=" * 84)
print("PostProcessor user (story mode, typical)")
pp_user = (
    f"【第12章正文】\n{'C'*4600}\n\n"
    f"【当前人物状态账本】\n{'S'*1200}\n\n【当前关系账本】\n{'R'*900}\n\n"
    f"【最近事件】\n{'E'*600}\n\n【待达成的剧情节点】\n{'P'*300}\n\n"
    f"【人物维度目录】\n{'D'*200}\n\n【已记录的真假话/世界事实】\n{'C'*400}\n\n"
    f"【卷内现场状态（若存在）】\n{{}}\n\n请输出纯小说的记忆增量。"
)
L("PostProcessor user (story, typical)", pp_user)
L("PostProcessor TOTAL (story)", "x" * (len(ChapterPostProcessor.STORY_SYSTEM) + len(pp_user)))
