import os, re, io, json, subprocess, zipfile, tarfile, collections

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
SUB = os.path.join(ROOT, "《越冬洄游》作者：博尔赫叁(补车+番外1)")
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\windows\system32\tar.exe"

# ---------- reading ----------
def read_dzip_tar(p):
    with tarfile.open(p, "r:*") as t:
        m = t.getmembers()[0]
        b = t.extractfile(m).read()
        mn = m.name
    for a, c in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                 (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
        b = b.replace(a, c)
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        for i in z.infolist():
            if not i.filename.endswith("/"):
                return z.read(i.filename), mn, len(z.infolist())
    raise RuntimeError("empty")

def read_zip(p):
    with zipfile.ZipFile(p) as z:
        members = [(i.filename, i.file_size, i.compress_size, i.compress_type, bool(i.flag_bits & 1)) for i in z.infolist()]
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
                            return z2.read(j.filename), members
            else:
                return d, members
    raise RuntimeError("empty")

def read_rar(p):
    r = subprocess.run([TAR, "-xOf", p], capture_output=True)
    return r.stdout.lstrip(b"\x00"), r.returncode

TAG = re.compile(r"<[^>]+>")
def html_to_text(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</(p|div|h[1-6]|li|tr)>", "\n", h)
    h = TAG.sub("", h)
    import html as _h
    h = _h.unescape(h)
    return h

def read_epub(p):
    """Return (text, detail) from an EPUB: concat HTML/XHTML spine members."""
    parts = []
    detail = {}
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        detail["members"] = len(names)
        hist = collections.Counter(os.path.splitext(n)[1].lower() or "(none)" for n in names)
        detail["hist"] = dict(hist)
        docs = sorted([n for n in names if n.lower().endswith((".html", ".xhtml", ".htm"))])
        detail["docs"] = len(docs)
        total = 0
        for n in docs:
            raw = z.read(n)
            total += len(raw)
            for enc in ("utf-8", "gb18030", "utf-16"):
                try:
                    s = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    s = None
            if s is None:
                s = raw.decode("utf-8", "replace")
            parts.append(f"\n\n{html_to_text(s)}")
        detail["html_bytes"] = total
    return "".join(parts), detail

def get_blob(name):
    p = os.path.join(ROOT, name)
    low = name.lower()
    if low.endswith(".txt"):
        return open(p, "rb").read(), ("plain txt", None)
    if low.endswith(".rar"):
        b, rc = read_rar(p)
        return b, ("RAR5 → bsdtar -xOf → txt（剥离 65514 字节 NUL 前导）", {"rc": rc})
    if low.endswith(".tar"):
        b, mn, n = read_dzip_tar(p)
        return b, ("TAR(ustar) → 成员 .dzip（UA→PK 修复为 ZIP）→ txt", {"member": mn, "n": n})
    if low.endswith(".zip"):
        b, mem = read_zip(p)
        return b, ("ZIP → 成员", {"members": mem})
    if low.endswith(".epub"):
        b, det = read_epub(p)
        return b.encode("utf-8"), ("EPUB(ZIP) → 全部 HTML/XHTML 成员去标签拼接", det)
    raise ValueError(name)

def decode_main(blob):
    if blob[:3] == b"\xef\xbb\xbf":
        return blob.decode("utf-8-sig"), "utf-8-sig", "UTF-8 BOM (EF BB BF)"
    if blob[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return blob.decode("utf-16"), "utf-16", "UTF-16 BOM (" + blob[:2].hex(" ").upper() + ")"
    try:
        return blob.decode("utf-8"), "utf-8", "无 BOM；整文件严格 UTF-8 通过"
    except UnicodeDecodeError:
        pass
    try:
        return blob.decode("gb18030"), "gb18030", "无 BOM；整文件严格 GB18030 通过"
    except UnicodeDecodeError:
        return blob.decode("gb18030", errors="replace"), "gb18030*", "两种严格解码均失败（有替换字符）"

def strict_report(blob):
    out = {}
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16"):
        try:
            blob.decode(enc)
            out[enc] = "通过"
        except UnicodeDecodeError as e:
            out[enc] = f"失败@{e.start}"
    return out

# ---------- chapter patterns (priority order) ----------
CH = r"[0-9零一二三四五六七八九十百千万两〇]"
PATS = collections.OrderedDict([
    ("第X章/回/节（可带 ☆、 或数字前缀）", re.compile(rf"^[ \t\u3000]*(?:[☆★◇◆]\s*[、.]?\s*)?(?:\d{{1,5}}\s*[、.．]?\s*)?第\s*{CH}+\s*[章回节]", re.M)),
    ("Chapter N", re.compile(r"^[ \t\u3000]*Chapter\s+\d+", re.M)),
    ("卷X（卷/部/篇）", re.compile(rf"^[ \t\u3000]*卷\s*{CH}+", re.M)),
    ("裸编号行 NN / N.", re.compile(r"^[ \t\u3000]*\d{1,4}\s*[.．、]?\s*$", re.M)),
    ("☆、标题", re.compile(r"^[ \t\u3000]*[☆★]\s*、", re.M)),
])

def analyse(name):
    blob, how = get_blob(name)
    text, codec, reason = decode_main(blob)
    repl = text.count("\ufffd")
    counts = {}
    ex = {}
    for k, rx in PATS.items():
        ms = list(rx.finditer(text))
        counts[k] = len(ms)
        e = []
        for m in ms[:3]:
            j = text.find("\n", m.start())
            e.append(text[m.start():j if j > 0 else m.start() + 50].strip()[:70])
        ex[k] = e
    primary = next((k for k in PATS if counts[k] >= 5), None)
    nchap = counts[primary] if primary else 0
    ms = list(PATS[primary].finditer(text)) if primary else []
    last = []
    for m in ms[-2:]:
        j = text.find("\n", m.start())
        last.append(text[m.start():j if j > 0 else m.start() + 50].strip()[:70])
    first_pos = ms[0].start() if ms else None
    header = text[:first_pos] if first_pos is not None else text[:600]
    hlines = [l.strip() for l in header.splitlines() if l.strip()][:8]

    JUNK = ["http://", "https://", "www.", "小说网", "笔趣阁", "请记住本站", "最新章节",
            "txt下载", "TXT下载", "手机版", "免费阅读", "首发", "加入书架", "全文字",
            "无弹窗", "更新最快", "本文由", "整理制作", "内容版权归作者所有",
            "本作品来自互联网", "书香门第", "派派", "久久小说", "禁忌书屋", "&#8226;", "解压码"]
    junk = {j: text.count(j) for j in JUNK if text.count(j) > 0}

    return {
        "name": name, "mb": round(os.path.getsize(os.path.join(ROOT, name)) / 1024 / 1024, 2),
        "bytes": os.path.getsize(os.path.join(ROOT, name)),
        "blob": len(blob), "chars": len(text), "codec": codec, "reason": reason,
        "repl": repl, "strict": strict_report(blob), "counts": counts,
        "primary": primary, "nchap": nchap, "ex": ex, "ex_primary": ex.get(primary, []),
        "last": last, "hlines": hlines, "junk": junk, "how": how[0],
    }

names = sorted([f for f in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, f))])
res = {}
for n in names:
    res[n] = analyse(n)
    print("ok", n, flush=True)

with open(os.path.join(OUT, "final.json"), "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
print("DONE", len(res))
