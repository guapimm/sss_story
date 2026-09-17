import os, io, zipfile, tarfile, subprocess, json

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
L = []

L.append("## ZIP archive member listings (Python stdlib zipfile) ##")
for name in sorted(os.listdir(ROOT)):
    if not name.lower().endswith(".zip"):
        continue
    p = os.path.join(ROOT, name)
    L.append(f"\n=== {name}  ({os.path.getsize(p)} bytes) ===")
    try:
        with zipfile.ZipFile(p) as z:
            z.testzip()
            for i in z.infolist():
                flag_utf8 = bool(i.flag_bits & 0x800)
                raw = i.orig_filename
                guess = None
                try:
                    guess = raw.encode("cp437").decode("gbk")
                except Exception:
                    pass
                L.append(f"   member={i.filename!r} size={i.file_size} comp={i.compress_size} "
                         f"method={i.compress_type} utf8flag={flag_utf8} gbk_guess={guess!r}")
    except Exception as e:
        L.append(f"   FAILED: {type(e).__name__}: {e}")

L.append("\n\n## EPUB archive structure (Python stdlib zipfile) ##")
for name in sorted(os.listdir(ROOT)):
    if not name.lower().endswith(".epub"):
        continue
    p = os.path.join(ROOT, name)
    L.append(f"\n=== {name}  ({os.path.getsize(p)} bytes) ===")
    try:
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            L.append(f"   total members: {len(names)}")
            ext_hist = {}
            for n in names:
                e = os.path.splitext(n)[1].lower() or "(none)"
                ext_hist[e] = ext_hist.get(e, 0) + 1
            L.append(f"   extension histogram: {ext_hist}")
            for n in names[:25]:
                L.append(f"     - {n!r} size={z.getinfo(n).file_size}")
            # mimetype check
            if "mimetype" in names:
                L.append(f"   mimetype = {z.read('mimetype')[:60]!r}")
            # find container.xml / opf
            for cand in ("META-INF/container.xml",):
                if cand in names:
                    L.append(f"   {cand} = {z.read(cand)[:300]!r}")
            opfs = [n for n in names if n.lower().endswith(".opf")]
            for o in opfs[:2]:
                L.append(f"   OPF {o}: {z.read(o)[:400]!r}")
    except Exception as e:
        L.append(f"   FAILED: {type(e).__name__}: {e}")

with open(os.path.join(OUT, "archives.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
