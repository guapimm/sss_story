import os, json, collections

OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
with open(os.path.join(OUT, "full_report.json"), encoding="utf-8") as f:
    R = json.load(f)

print(f"total entries: {len(R)}")
print("\n## chosen codec distribution")
c = collections.Counter(v.get("chosen") for v in R.values())
for k, n in c.most_common():
    print(f"  {k}: {n}")

print("\n## files with replacement chars > 0")
for k, v in sorted(R.items()):
    if v.get("repl_chars", 0) > 0:
        print(f"  {k}: repl={v['repl_chars']} chosen={v.get('chosen')} codecs={v.get('codecs')}")

print("\n## files with 0 chapters detected")
for k, v in sorted(R.items()):
    if v.get("nchap", 0) == 0 and "error" not in v:
        print(f"  {k}: chosen={v.get('chosen')} chars={v.get('chars')} codecs={v.get('codecs')}")

print("\n## errors")
for k, v in sorted(R.items()):
    if "error" in v:
        print(f"  {k}: {v['error']}")

print("\n## BOM distribution")
for k, n in collections.Counter(str(v.get("bom")) for v in R.values()).most_common():
    print(f"  {k}: {n}")

print("\n## junk-bearing files")
for k, v in sorted(R.items()):
    if v.get("junk"):
        print(f"  {k}: {v['junk']}")
