import os, re, json, io, zipfile, tarfile, subprocess, collections

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\windows\system32\tar.exe"
L = []
CH = r"[0-9零一二三四五六七八九十百千万两〇]"
P = re.compile(rf"^[ \t\u3000]*(?:[☆★◇◆]\s*[、.]?\s*)?(?:\d{{1,5}}\s*[、.．]?\s*)?第\s*{CH}+\s*[章回节]", re.M)

def blob_of(name):
    p = os.path.join(ROOT, name)
    if name.lower().endswith(".txt"):
        return open(p, "rb").read()
    if name.lower().endswith(".rar"):
        return subprocess.run([TAR, "-xOf", p], capture_output=True).stdout.lstrip(b"\x00")
    if name.lower().endswith(".tar"):
        with tarfile.open(p, "r:*") as t:
            b = t.extractfile(t.getmembers()[0]).read()
        for a, c in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                     (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
            b = b.replace(a, c)
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            for i in z.infolist():
                if not i.filename.endswith("/"):
                    return z.read(i.filename)
    if name.lower().endswith(".zip"):
        with zipfile.ZipFile(p) as z:
            for i in z.infolist():
                if i.filename.endswith("/"):
                    continue
                d = z.read(i.filename)
                if d[:2] == b"UA":
                    for a, c in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                                 (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
                        d = d.replace(a, c)
                    with zipfile.ZipFile(io.BytesIO(d)) as z2:
                        for j in z2.infolist():
                            if not j.filename.endswith("/"):
                                return z2.read(j.filename)
                else:
                    return d
    raise ValueError(name)

def dec(b):
    if b[:3] == b"\xef\xbb\xbf":
        return b.decode("utf-8-sig")
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gb18030")

CHECK = ["《天涯客》作者：priest.txt", "《杀破狼》作者：priest.txt", "乱世为王.txt",
         "轮回乐园.txt", "《剑来》（网络版精校全本）.txt", "斗破苍穹.rar", "《某某》作者：木苏里.txt"]

for nm in CHECK:
    t = dec(blob_of(nm))
    ms = list(P.finditer(t))
    L.append("=" * 70)
    L.append(f"{nm}  matches={len(ms)}  chars={len(t)}")
    # show positions and whether trailing '...' (TOC indicator)
    toc = 0
    body = 0
    for m in ms:
        j = t.find("\n", m.start())
        line = t[m.start():j if j > 0 else m.start() + 60].strip()
        if line.endswith("...") or line.endswith("…") or "..." in line:
            toc += 1
        else:
            body += 1
    L.append(f"  TOC-looking (trailing ...): {toc}")
    L.append(f"  body-looking             : {body}")
    L.append(f"  first 5 matches: {[t[m.start():t.find(chr(10), m.start())].strip()[:50] for m in ms[:5]]}")
    L.append(f"  last 5 matches : {[t[m.start():t.find(chr(10), m.start())].strip()[:50] for m in ms[-5:]]}")
    # distribution of match positions
    if ms:
        pos = [m.start() for m in ms]
        L.append(f"  match span: {pos[0]} .. {pos[-1]} of {len(t)}")
        # detect a dense cluster at the start (= TOC)
        first20 = pos[:20]
        L.append(f"  first 20 positions: {first20}")

with open(os.path.join(OUT, "chapcheck.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
