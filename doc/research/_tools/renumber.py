# -*- coding: utf-8 -*-
"""Renumber the 4.1x sub-sections in the report to a clean 4.2..4.11 sequence."""
import io, re

P = r"E:\AI_project\story_commandcode\doc\research\04-agents-and-prompts.md"
t = io.open(P, encoding="utf-8").read()

# Step 1: renumber the two high-numbered sections first, using placeholders
t = t.replace("### 4.2 为什么", "### @@SEC10@@ 为什么")
t = t.replace("### 4.3 人物关系单一", "### @@SEC11@@ 人物关系单一")

# Step 2: renumber the lettered ones
for old, new in [("4.1b", "4.2"), ("4.1c", "4.3"), ("4.1d", "4.4"), ("4.1e", "4.5"),
                 ("4.1f", "4.6"), ("4.1g", "4.7"), ("4.1h", "4.8"), ("4.1i", "4.9")]:
    # headings
    t = t.replace(f"### {old} ", f"### {new} ")
    # cross references
    t = t.replace(f"§{old}", f"§{new}")
    t = t.replace(f"（{old}）", f"（{new}）")

# Step 3: resolve placeholders
t = t.replace("### @@SEC10@@ 为什么", "### 4.10 为什么")
t = t.replace("### @@SEC11@@ 人物关系单一", "### 4.11 人物关系单一")

# Step 4: fix any remaining references to the old 4.2/4.3 in body text that meant the renumbered ones
t = t.replace("见 §4.2（`must_review`", "见 §4.4（`must_review`")

io.open(P, "w", encoding="utf-8", newline="\n").write(t)

# report
print("lines:", t.count("\n"))
for ln in t.split("\n"):
    if re.match(r"^#{2,3} ", ln):
        print("  ", ln)
