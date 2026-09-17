import os, tarfile, io, zipfile, json

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
lines = []

for name in sorted(os.listdir(ROOT)):
    if not name.lower().endswith(".tar"):
        continue
    p = os.path.join(ROOT, name)
    with tarfile.open(p, "r:*") as t:
        for m in t.getmembers():
            f = t.extractfile(m)
            head = f.read(64)
            lines.append(f"{name}")
            lines.append(f"   member={m.name!r} size={m.size}")
            lines.append(f"   magic[:16]={head[:16].hex(' ')}")
            lines.append(f"   ascii={head[:16]!r}")
            f.seek(0)
            blob = f.read()
            # is it a zip?
            if blob[:2] == b"PK":
                lines.append("   -> PK ZIP signature!")
                try:
                    z = zipfile.ZipFile(io.BytesIO(blob))
                    for i in z.infolist():
                        lines.append(f"      zip-member: {i.filename!r} size={i.file_size} comp={i.compress_type}")
                except Exception as e:
                    lines.append(f"      zipfile failed: {e}")
            else:
                lines.append(f"   -> NOT a zip. first 32 bytes hex: {blob[:32].hex(' ')}")
            lines.append("")

with open(os.path.join(OUT, "dzip_probe.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("ok")
