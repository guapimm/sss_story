import os, re, json, io, zipfile, tarfile, subprocess, collections

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\Windows\system32\tar.exe"
L = []

def read_any(name):
    p = os.path.join(ROOT, name)
    low = name.lower()
    if low.endswith(".txt"):
        return open(p, "rb").read()
    if low.endswith(".rar"):
        return subprocess.run([TAR, "-xOf", p], capture_output=True).stdout.lstrip(b"\x00")
    if low.endswith(".tar"):
        with tarfile.open(p, "r:*") as t:
            b = t.extractfile(t.getmembers()[0]).read()
        for a, c in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                     (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
            b = b.replace(a, c)
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            for i in z.infolist():
                if not i.filename.endswith("/"):
                    return z.read(i.filename)
    if low.endswith(".zip"):
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
    return b""

FILES = [
    "《天涯客》作者：priest.txt",
    "《杀破狼》作者：priest.txt",
    "《破云2.吞海》作者：淮上（补肉版+5番外）.txt",
    "乱世为王.txt",
    "完结｜《溺酒》作者：口卡.txt",
    "《某某》作者：木苏里.txt",
]

for nm in FILES:
    blob = read_any(nm)
    if blob[:3] == b"\xef\xbb\xbf":
        text = blob.decode("utf-8-sig")
        enc = "utf-8-sig"
    elif blob[:2] == b"\xff\xfe":
        text = blob.decode("utf-16")
        enc = "utf-16 (LE BOM)"
    elif blob[:2] == b"\xfe\xff":
        text = blob.decode("utf-16")
        enc = "utf-16 (BE BOM)"
    else:
        try:
            text = blob.decode("utf-8")
            enc = "utf-8"
        except UnicodeDecodeError:
            text = blob.decode("gb18030")
            enc = "gb18030"

    L.append("=" * 78)
    L.append(f"FILE: {nm}")
    L.append(f"  codec={enc}  chars={len(text)}")
    lines = text.splitlines()
    L.append(f"  total lines={len(lines)}")
    L.append(f"  first 12 non-blank lines:")
    n = 0
    for ln in lines:
        if ln.strip():
            L.append(f"     {ln.strip()[:90]!r}")
            n += 1
            if n >= 12:
                break

    # candidate chapter patterns
    pats = {
        "di_X_zhang": re.compile(rf"^[ \t\u3000]*第[ \t\u3000]*[0-9零一二三四五六七八九十百千万两〇]+[ \t\u3000]*[章回节]", re.M),
        "juan_X": re.compile(rf"^[ \t\u3000]*卷[ \t\u3000]*[0-9零一二三四五六七八九十百千万两〇]+", re.M),
        "bare_number": re.compile(r"^[ \t\u3000]*\d{1,4}[ \t\u3000]*$", re.M),
        "bare_number_dot": re.compile(r"^[ \t\u3000]*\d{1,4}[\.、][ \t\u3000]*\S", re.M),
        "paren_num": re.compile(r"^[ \t\u3000]*[（(]\s*\d+\s*[)）]", re.M),
        "chapter_en": re.compile(r"^[ \t]*Chapter[ \t]+\d+", re.M),
        "marker_xing": re.compile(r"^[ \t\u3000]*[☆★◇◆○●]", re.M),
        "section": re.compile(r"^[ \t\u3000]*[Ss]ection[ \t]*\d+", re.M),
        "part": re.compile(r"^[ \t\u3000]*(?:Part|PART)[ \t]+\d+", re.M),
    }
    counts = {}
    for k, rx in pats.items():
        ms = list(rx.finditer(text))
        counts[k] = len(ms)
    L.append(f"  pattern counts: {counts}")

    # show 3 examples of the best few patterns
    for k, rx in pats.items():
        ms = list(rx.finditer(text))
        if ms:
            L.append(f"    {k} ({len(ms)}):")
            for m in ms[:3]:
                e = text.find("\n", m.start())
                L.append(f"       {text[m.start():e if e>0 else m.start()+50].strip()[:80]!r}")
    # sample every ~10% of the file to show what headings look like
    L.append("  ---- sampled lines at deciles (to spot the real heading form) ----")
    for frac in (0.1, 0.25, 0.5, 0.75, 0.9):
        idx = int(len(lines) * frac)
        seg = [x.strip() for x in lines[max(0, idx - 4):idx + 6]]
        L.append(f"    @{frac:.2f}: {[s[:60] for s in seg if s][:6]}")

with open(os.path.join(OUT, "conventions.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
