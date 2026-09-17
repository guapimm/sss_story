import os, re, json, io, zipfile, tarfile, subprocess

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\Windows\system32\tar.exe"
L = []

with open(os.path.join(OUT, "full_report.json"), encoding="utf-8") as f:
    R = json.load(f)

L.append("### Files whose chosen codec had replacement chars, or 0 chapters ###")
targets = [k for k, v in R.items() if v.get("repl_chars", 0) > 0 or (v.get("nchap", 0) == 0 and "error" not in v)]
for t in targets:
    v = R[t]
    L.append(f"  - {t}  chosen={v.get('chosen')} repl={v.get('repl_chars')} nchap={v.get('nchap')} bom={v.get('bom')}")
L.append("")

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

# ---- deep dive on the suspicious ones ----
for t in targets:
    blob = read_any(t)
    L.append("=" * 78)
    L.append(f"FILE: {t}")
    L.append(f"  blob bytes = {len(blob)}")
    L.append(f"  first 32 bytes hex: {blob[:32].hex(' ')}")
    L.append(f"  first 32 bytes repr: {blob[:32]!r}")

    # per-codec strict decode of the WHOLE file
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "utf-16-le", "utf-16-be", "big5"):
        try:
            s = blob.decode(enc)
            L.append(f"  [whole] {enc:10} OK  chars={len(s)} repl={s.count(chr(0xFFFD))}")
        except UnicodeDecodeError as e:
            L.append(f"  [whole] {enc:10} FAIL @{e.start} ({e.reason})")

    # strict utf-16 with even-length handling
    for enc in ("utf-16-le", "utf-16-be"):
        b = blob if len(blob) % 2 == 0 else blob[:-1]
        try:
            s = b.decode(enc)
            L.append(f"  [whole/even] {enc:10} OK chars={len(s)}")
        except UnicodeDecodeError as e:
            L.append(f"  [whole/even] {enc:10} FAIL @{e.start}")

with open(os.path.join(OUT, "deep.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
