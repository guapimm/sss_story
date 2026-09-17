import os, json, collections, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
with open(os.path.join(OUT, "final.json"), encoding="utf-8") as f:
    R = json.load(f)

# markdown table rows
rows = []
for k, v in sorted(R.items(), key=lambda kv: -kv[1]["bytes"]):
    ext = os.path.splitext(k)[1].lower()
    ex = " ⏎ ".join(v["ex_primary"]) if v["ex_primary"] else "（无）"
    rows.append(f"| `{k}` | {ext} | {v['mb']:.2f} | {v['codec']} | {v['nchap']} | {ex} |")

print("### BIG TABLE")
print("| 文件 | 格式 | MB | 编码 | 章数 | 示例标题 |")
print("|---|---|---|---|---|---|")
for r in rows:
    print(r)

print()
print("### header blocks for the 8+ sampled files")
SAMPLE = ["轮回乐园.txt", "《剑来》（网络版精校全本）.txt", "神秘复苏.txt", "大奉打更人.txt",
          "剑来.rar", "大奉打更人.rar", "《道诡异仙》作者：狐尾的笔.txt",
          "《小天师》作者：婻书.txt", "再世权臣.txt", "逆天邪神 (火星引力) .epub",
          "诛仙.epub", "《不对付》作者：回南雀.txt", "《尾钩》作者：塞纳左岸（larivegauche）.txt",
          "《遇蛇》作者：溯痕.txt", "《烧》作者：初岛.txt"]
for s in SAMPLE:
    if s in R:
        v = R[s]
        print(f"\n{s} | {v['mb']}MB | {v['codec']} | 章={v['nchap']} | {v['reason']}")
        print(f"  标题行样本: {v['ex_primary']}")
        print(f"  末章: {v['last']}")
        print(f"  头部块: {v['hlines']}")
        print(f"  垃圾: {v['junk']}")

print()
print("### 全文无替换字符确认")
bad = [(k, v["repl"]) for k, v in R.items() if v["repl"] > 0]
print("replacement>0:", bad if bad else "无（全部 65 个文件均 0 替换字符）")

print()
print("### strict decode matrix")
for k, v in sorted(R.items()):
    print(f"{v['mb']:7.2f} {k}")
    print(f"          {v['strict']}")
