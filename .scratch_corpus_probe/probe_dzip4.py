import os, io, re, struct, zipfile, tarfile, json, collections

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
L = []

SUBS = [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
        (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]

def repair_dzip(blob):
    out = blob
    for a, b in SUBS:
        out = out.replace(a, b)
    return out

CH = r"[0-9零一二三四五六七八九十百千万两〇]"
CHAP = re.compile(rf"^[ \t\u3000]*第[ \t\u3000]*{CH}+[ \t\u3000]*[章节回]", re.M)

L.append("## DZIP repair verification (all 5 .tar + 2 nested .zip) ##")

targets = []
for name in sorted(os.listdir(ROOT)):
    if name.lower().endswith(".tar"):
        targets.append((name, "tar"))
    elif name.lower().endswith(".zip"):
        targets.append((name, "zip"))

for name, kind in targets:
    p = os.path.join(ROOT, name)
    blobs = []
    if kind == "tar":
        with tarfile.open(p, "r:*") as t:
            for m in t.getmembers():
                blobs.append((m.name, t.extractfile(m).read()))
    else:
        with zipfile.ZipFile(p) as z:
            for i in z.infolist():
                if i.filename.endswith("/"):
                    continue
                data = z.read(i.filename)
                if i.filename.lower().endswith(".dzip") or data[:2] == b"UA":
                    blobs.append((i.filename, data))
    if not blobs:
        continue
    L.append(f"\n=== {name} ({kind}) ===")
    for mname, blob in blobs:
        fixed = repair_dzip(blob)
        try:
            with zipfile.ZipFile(io.BytesIO(fixed)) as z:
                z.testzip()
                L.append(f"  REPAIR OK -> {mname!r}")
                for i in z.infolist():
                    L.append(f"     member={i.filename!r} usize={i.file_size} csize={i.compress_size} method={i.compress_type}")
                    data = z.read(i.filename)
                    for enc in ("utf-8", "gb18030", "utf-16"):
                        try:
                            s = data.decode(enc)
                            L.append(f"     decode {enc}: OK  chars={len(s)}")
                            if enc == "utf-8":
                                heads = CHAP.findall(s)
                                ms = list(CHAP.finditer(s))
                                L.append(f"     chapter-heading matches: {len(ms)}")
                                for mm in ms[:3]:
                                    e = s.find("\n", mm.start())
                                    L.append(f"        VERBATIM: {s[mm.start():e if e>0 else mm.start()+60].strip()[:90]!r}")
                                L.append(f"     header block (first 6 nonblank lines):")
                                cnt = 0
                                for ln in s.splitlines():
                                    if ln.strip():
                                        L.append(f"        {ln.strip()[:100]!r}")
                                        cnt += 1
                                        if cnt >= 6:
                                            break
                            break
                        except UnicodeDecodeError:
                            continue
        except Exception as e:
            L.append(f"  REPAIR FAILED {mname!r}: {type(e).__name__}: {e}")

with open(os.path.join(OUT, "dzip_verify.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
