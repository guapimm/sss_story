import os, io, json

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"

def dump(p, n=512):
    with open(p, "rb") as f:
        return f.read(n)

lines = []
for name in sorted(os.listdir(ROOT)):
    if not name.lower().endswith(".tar"):
        continue
    p = os.path.join(ROOT, name)
    head = dump(p, 512)
    lines.append("=" * 78)
    lines.append(f"FILE: {name}  size={os.path.getsize(p)}")
    lines.append(f"offset 0..127 hex : {head[:128].hex(' ')}")
    lines.append(f"offset 0..63  repr: {head[:64]!r}")
    # ustar magic check at 257
    full = dump(p, 512)
    lines.append(f"magic@257: {full[257:265]!r}  (ustar expected)")
    # try tarfile on the real file
    import tarfile
    try:
        with tarfile.open(p, "r:*") as t:
            members = t.getmembers()
            lines.append(f"tarfile OK, {len(members)} members, format={t.format}")
            for m in members[:20]:
                lines.append(f"   - {m.name!r} size={m.size} type={m.type!r}")
    except Exception as e:
        lines.append(f"tarfile FAILED: {type(e).__name__}: {e}")

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "tar_probe.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written")
