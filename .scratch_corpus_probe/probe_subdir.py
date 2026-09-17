import os, io, zipfile, json

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
L = []

sub = os.path.join(ROOT, "《越冬洄游》作者：博尔赫叁(补车+番外1)")
for name in os.listdir(sub):
    p = os.path.join(sub, name)
    L.append(f"ENTRY: {name!r} isdir={os.path.isdir(p)} size={os.path.getsize(p) if os.path.isfile(p) else '-'}")

zp = os.path.join(sub, "《越冬洄游》作者：博尔赫叁(补车+番外1).zip")
L.append(f"\nZIP: {zp}")
with open(zp, "rb") as f:
    head = f.read(16)
L.append(f"magic: {head.hex(' ')}")

try:
    with zipfile.ZipFile(zp) as z:
        for i in z.infolist():
            L.append(f"  member={i.filename!r} usize={i.file_size} csize={i.compress_size} "
                     f"method={i.compress_type} flag={i.flag_bits:#x} encrypted={bool(i.flag_bits & 1)}")
except Exception as e:
    L.append(f"  zipfile FAILED: {type(e).__name__}: {e}")

# try reading without password
try:
    with zipfile.ZipFile(zp) as z:
        for i in z.infolist():
            if i.filename.endswith("/"):
                continue
            try:
                d = z.read(i.filename)
                L.append(f"  READ OK {i.filename!r} -> {len(d)} bytes  head={d[:60]!r}")
            except Exception as e:
                L.append(f"  READ FAIL {i.filename!r}: {type(e).__name__}: {e}")
                # try the hinted password
                for pw in (b"2023",):
                    try:
                        d = z.read(i.filename, pwd=pw)
                        L.append(f"     with password {pw!r}: OK {len(d)} bytes head={d[:80]!r}")
                    except Exception as e2:
                        L.append(f"     with password {pw!r}: FAIL {type(e2).__name__}: {e2}")
except Exception as e:
    L.append(f"outer: {e}")

with open(os.path.join(OUT, "subdir.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
