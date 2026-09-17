import os, json, re, difflib, hashlib

OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
with open(os.path.join(OUT, "txt_report.json"), encoding="utf-8") as f:
    R = json.load(f)

L = []
L.append("## TXT encoding + chapter digests ##")
for name, d in sorted(R.items(), key=lambda kv: kv[1]["size_mb"]):
    eh = d["enc_head"]
    ok = [k for k, v in eh["ok"].items() if v]
    L.append(f"\n### {name}  [{d['size_mb']} MB, {d['size_bytes']} B, {d['char_len']} chars]")
    L.append(f"  BOM={eh['bom']}")
    L.append(f"  head-200KB decodes cleanly as: {ok}")
    L.append(f"  replacement chars (head, per codec): {eh['replacement']}")
    L.append(f"  chosen codec = {d['chosen_codec']}; replacement chars whole file = {d['replacement_chars_whole']}")
    L.append(f"  pattern counts = {d['counts']}")
    L.append(f"  DOMINANT = {d['dominant']}")
    for k, ex in d["examples"].items():
        if ex:
            L.append(f"    examples[{k}]:")
            for e in ex:
                L.append(f"       {e!r}")
    L.append(f"  header lines: {d['header_lines']}")
    L.append(f"  junk hits: {d['junk_hits']}")
    L.append(f"  adult-marker counts: {d['adult_hits']}")

with open(os.path.join(OUT, "txt_digest.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("digest written", len(R))
