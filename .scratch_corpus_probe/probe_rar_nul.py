import os, subprocess, io, zipfile, tarfile, re, collections

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\Windows\system32\tar.exe"
L = []

p = os.path.join(ROOT, "大奉打更人.rar")
r = subprocess.run([TAR, "-xOf", p], capture_output=True)
raw = r.stdout
L.append(f"bsdtar -xOf stdout len={len(raw)}  stderr={r.stderr[:200]!r} rc={r.returncode}")
L.append(f"first 64 bytes hex: {raw[:64].hex(' ')}")
nul = sum(1 for b in raw if b == 0)
L.append(f"total NUL bytes in stream: {nul}")
# where are the nuls?
runs = []
i = 0
while i < len(raw):
    if raw[i] == 0:
        j = i
        while j < len(raw) and raw[j] == 0:
            j += 1
        runs.append((i, j - i))
        i = j
    else:
        i += 1
L.append(f"nul runs: count={len(runs)} first 10={runs[:10]}")
# strip nuls and check
stripped = raw.replace(b"\x00", b"")
L.append(f"after stripping NULs: len={len(stripped)}")
L.append(f"stripped head: {stripped[:200]!r}")
for enc in ("utf-8", "gb18030"):
    try:
        s = stripped.decode(enc)
        L.append(f"  decode {enc}: OK chars={len(s)}")
    except Exception as e:
        L.append(f"  decode {enc}: FAIL {e}")

# Compare with a second rar and check tail (RAR5 may pad at end)
for nm in ["剑来.rar", "大奉打更人.rar", "仙逆.rar"]:
    r2 = subprocess.run([TAR, "-xOf", os.path.join(ROOT, nm)], capture_output=True)
    d = r2.stdout
    L.append(f"\n{nm}: len={len(d)} nul={d.count(0)} head16={d[:16].hex(' ')} tail16={d[-16:].hex(' ')}")

# Does bsdtar have a sparse issue? check -s or use --no-same-owner? try listing with size
for nm in ["大奉打更人.rar"]:
    r3 = subprocess.run([TAR, "-tvf", os.path.join(ROOT, nm)], capture_output=True)
    L.append(f"\nverbose listing {nm}: {r3.stdout!r}")

with open(os.path.join(OUT, "rar_nul.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
