import os, re, json, hashlib, subprocess, io, zipfile, tarfile

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\Windows\system32\tar.exe"
L = []

def core(n):
    b = os.path.splitext(n)[0]
    b = b.replace("《", "").replace("》", "").replace("（", "(").replace("）", ")")
    b = re.split(r"作者|by|BY", b)[0]
    b = re.sub(r"\(.*?\)", "", b)
    b = re.sub(r"[\s_｜|].*$", "", b)
    b = b.replace("完结", "").strip("｜| -")
    return b.strip()

names = sorted(os.listdir(ROOT))
groups = {}
for n in names:
    p = os.path.join(ROOT, n)
    if os.path.isfile(p):
        groups.setdefault(core(n), []).append(n)

L.append("## Title-level duplicate groups ##")
for k, v in sorted(groups.items()):
    if len(v) > 1:
        L.append(f"\n### group key: {k!r}  ({len(v)} files)")
        for x in v:
            L.append(f"   - {x}   {round(os.path.getsize(os.path.join(ROOT,x))/1024/1024,2)} MB")

# ---- content-level signature extraction for the 5 duplicate groups ----
CH = r"[0-9零一二三四五六七八九十百千万两〇]"
CHAP = re.compile(rf"^[ \t\u3000]*第[ \t\u3000]*{CH}+[ \t\u3000]*[章节回]", re.M)

def read_rar_member(path):
    r = subprocess.run([TAR, "-xOf", path], capture_output=True)
    return r.stdout

def read_tar_dzip(path):
    with tarfile.open(path, "r:*") as t:
        m = t.getmembers()[0]
        blob = t.extractfile(m).read()
    for a, b in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                 (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
        blob = blob.replace(a, b)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for i in z.infolist():
            if not i.filename.endswith("/"):
                return z.read(i.filename)

def read_zip_member(path):
    with zipfile.ZipFile(path) as z:
        best = None
        for i in z.infolist():
            if i.filename.endswith("/"):
                continue
            data = z.read(i.filename)
            if data[:2] == b"UA":
                for a, b in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                             (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
                    data = data.replace(a, b)
                try:
                    with zipfile.ZipFile(io.BytesIO(data)) as z2:
                        for j in z2.infolist():
                            if not j.filename.endswith("/"):
                                return z2.read(j.filename)
                except Exception:
                    pass
            else:
                return data
    return b""

def decode_any(blob):
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return blob.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return blob.decode("utf-8", "replace"), "utf-8/replace"

def sig(name):
    p = os.path.join(ROOT, name)
    low = name.lower()
    if low.endswith(".txt"):
        with open(p, "rb") as f:
            blob = f.read()
    elif low.endswith(".rar"):
        blob = read_rar_member(p)
    elif low.endswith(".tar"):
        blob = read_tar_dzip(p)
    elif low.endswith(".zip"):
        blob = read_zip_member(p)
    else:
        return None
    text, enc = decode_any(blob)
    ms = list(CHAP.finditer(text))
    chaps = [text[m.start():text.find("\n", m.start()) if text.find("\n", m.start()) > 0 else m.start() + 40].strip() for m in ms]
    return {
        "name": name, "mb": round(os.path.getsize(p) / 1024 / 1024, 2),
        "bytes": os.path.getsize(p), "chars": len(text), "enc": enc,
        "nchap": len(chaps), "first3": chaps[:3], "last3": chaps[-3:],
        "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
        "head": text[:60].replace("\n", "\\n"),
    }

L.append("\n\n## Content-level comparison of duplicate groups ##")
for k, v in sorted(groups.items()):
    if len(v) < 2:
        continue
    L.append(f"\n### {k!r}")
    sigs = []
    for x in v:
        try:
            s = sig(x)
            sigs.append(s)
            L.append(f"   {x}")
            L.append(f"      container={round(s['mb'],2)}MB  decoded_chars={s['chars']}  enc={s['enc']}  chapters={s['nchap']}")
            L.append(f"      first3={s['first3']}")
            L.append(f"      last3={s['last3']}")
            L.append(f"      text_sha1_12={s['sha1']}  head={s['head']!r}")
        except Exception as e:
            L.append(f"   {x} -> FAILED {type(e).__name__}: {e}")

with open(os.path.join(OUT, "dupes.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
