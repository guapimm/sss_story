import os, json, collections

OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
with open(os.path.join(OUT, "final.json"), encoding="utf-8") as f:
    R = json.load(f)

def show(name):
    v = R[name]
    print(f"### {name}")
    print(f"  mb={v['mb']} codec={v['codec']} reason={v['reason']} repl={v['repl']}")
    print(f"  strict={v['strict']}")
    print(f"  primary={v['primary']} nchap={v['nchap']}")
    print(f"  counts={v['counts']}")
    print(f"  ex={v['ex_primary']}")
    print(f"  last={v['last']}")
    print(f"  header={v['hlines']}")
    print(f"  junk={v['junk']}")

# summary only: codecs
print("## CODEC SUMMARY")
c = collections.Counter(v["codec"] for v in R.values())
for k, n in c.most_common():
    print(f"  {k}: {n}")

print("\n## files > 1MB (the 8+ sample set), key facts")
big = sorted(R.items(), key=lambda kv: -kv[1]["bytes"])[:16]
for k, v in big:
    print(f"  {v['mb']:6} MB  {v['codec']:10} chap={v['nchap']:5} ({v['primary']}) {k}")

print("\n## ALL files, one line each")
for k, v in sorted(R.items(), key=lambda kv: -kv[1]["bytes"]):
    print(f"{v['mb']:7.2f} | {v['codec']:10} | {v['nchap']:5} | {(v['primary'] or '-')[:22]:22} | {k}")
