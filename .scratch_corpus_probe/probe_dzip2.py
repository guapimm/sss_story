import os, io, zipfile, tarfile, subprocess

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
L = []
TAR = r"C:\Windows\system32\tar.exe"

L.append("### DZIP signature repair test ###")
p = os.path.join(ROOT, "《尾钩》（实体书番外）.tar")
with tarfile.open(p, "r:*") as t:
    m = t.getmembers()[0]
    blob = t.extractfile(m).read()

for label, cand in [
    ("replace first2 with PK", b"PK" + blob[2:]),
    ("XOR first2 with 0x05", bytes(b ^ 0x05 for b in blob[:2]) + blob[2:]),
    ("XOR first2 with 0x1A", bytes(b ^ 0x1A for b in blob[:2]) + blob[2:]),
]:
    try:
        z = zipfile.ZipFile(io.BytesIO(cand))
        names = z.namelist()
        L.append(f"  {label}: OK -> {names[:5]}")
        info = z.infolist()[0]
        data = z.read(info.filename)
        L.append(f"     first member {info.filename!r} uncompressed={len(data)}")
        L.append(f"     head bytes: {data[:80].hex(' ')}")
        for enc in ("utf-8", "gb18030", "utf-16"):
            try:
                s = data[:4000].decode(enc)
                L.append(f"     decode {enc}: OK  sample={s[:120]!r}")
            except Exception as e:
                L.append(f"     decode {enc}: FAIL {e}")
    except Exception as e:
        L.append(f"  {label}: FAIL {type(e).__name__}: {e}")

L.append("")
L.append("### RAR member names (raw bytes -> gbk/utf8) ###")
for name in sorted(os.listdir(ROOT)):
    if not name.lower().endswith(".rar"):
        continue
    fp = os.path.join(ROOT, name)
    r = subprocess.run([TAR, "-tf", fp], capture_output=True)
    raw = r.stdout
    for line in raw.split(b"\n"):
        line = line.strip()
        if not line:
            continue
        dec = {}
        for enc in ("utf-8", "gbk", "gb18030", "cp936"):
            try:
                dec[enc] = line.decode(enc)
            except Exception:
                dec[enc] = None
        L.append(f"{name}  raw={line.hex(' ')}")
        for k, v in dec.items():
            L.append(f"     {k}: {v!r}")

with open(os.path.join(OUT, "dzip_rar.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
