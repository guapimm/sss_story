import os, re, io, json, subprocess, zipfile, tarfile, zlib

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\Windows\system32\tar.exe"

CH = r"[0-9零一二三四五六七八九十百千万两〇]"
CHAP = re.compile(rf"^[ \t\u3000]*第[ \t\u3000]*{CH}+[ \t\u3000]*[章节回]", re.M)
JUNK = ["http://", "https://", "www.", "小说网", "笔趣阁", "请记住本站", "最新章节",
        "txt下载", "TXT下载", "手机版", "免费阅读", "首发", "加入书架", "全文字",
        "无弹窗", "更新最快", "本文由", "整理制作", "内容版权归作者所有", "本作品来自互联网",
        "书香门第", "派派", "久久小说", "禁忌书屋", "同人小说网"]

def strip_nul(b):
    return b.lstrip(b"\x00")

def read_txt(p):
    with open(p, "rb") as f:
        return f.read(), os.path.getsize(p)

def read_rar(p):
    r = subprocess.run([TAR, "-xOf", p], capture_output=True)
    return strip_nul(r.stdout), os.path.getsize(p)

def read_dzip_tar(p):
    with tarfile.open(p, "r:*") as t:
        m = t.getmembers()[0]
        blob = t.extractfile(m).read()
        mname = m.name
    for a, b in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                 (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
        blob = blob.replace(a, b)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for i in z.infolist():
            if not i.filename.endswith("/"):
                return z.read(i.filename), os.path.getsize(p), mname
    raise RuntimeError("empty")

def read_zip(p):
    with zipfile.ZipFile(p) as z:
        for i in z.infolist():
            if i.filename.endswith("/"):
                continue
            data = z.read(i.filename)
            if data[:2] == b"UA":
                for a, b in [(b"UA\x03\x04", b"PK\x03\x04"), (b"UA\x01\x02", b"PK\x01\x02"),
                             (b"UA\x05\x06", b"PK\x05\x06"), (b"UA\x07\x08", b"PK\x07\x08")]:
                    data = data.replace(a, b)
                with zipfile.ZipFile(io.BytesIO(data)) as z2:
                    for j in z2.infolist():
                        if not j.filename.endswith("/"):
                            return z2.read(j.filename), os.path.getsize(p), i.filename
            else:
                return data, os.path.getsize(p), i.filename
    raise RuntimeError("empty")

def pick_codec(blob):
    """Report which codecs decode the first 200KB cleanly.

    The sample is trimmed by up to 3 trailing bytes so that a multi-byte
    character cut by the 200KB boundary cannot produce a false negative.
    """
    head = blob[:200_000]
    # trim partial trailing multibyte sequence
    for cut in range(0, 4):
        sample = head[: len(head) - cut] if cut else head
        if not sample:
            continue
        try:
            sample.decode("utf-8")
            break
        except UnicodeDecodeError:
            continue
    res = {}
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "big5"):
        try:
            sample.decode(enc)
            res[enc] = "clean"
        except UnicodeDecodeError as e:
            res[enc] = f"fail@{e.start}"
    bom = None
    if blob[:3] == b"\xef\xbb\xbf":
        bom = "UTF-8 BOM (EF BB BF)"
    elif blob[:2] == b"\xff\xfe":
        bom = "UTF-16LE BOM (FF FE)"
    elif blob[:2] == b"\xfe\xff":
        bom = "UTF-16BE BOM (FE FF)"
    return res, bom

def analyse(name, blob, container_bytes, extra=None):
    encs, bom = pick_codec(blob)
    candidates = []
    if bom and "UTF-8" in bom:
        candidates.append("utf-8-sig")
    if encs.get("utf-8") == "clean":
        candidates.append("utf-8")
    if encs.get("gb18030") == "clean":
        candidates.append("gb18030")
    if encs.get("utf-16") == "clean":
        candidates.append("utf-16")
    if not candidates:
        candidates = ["gb18030", "utf-8"]
    # score each candidate by replacement-char count over the WHOLE file
    scored = []
    for c in candidates:
        try:
            t = blob.decode(c)
            scored.append((t.count("\ufffd"), c, t))
        except UnicodeDecodeError:
            t = blob.decode(c, errors="replace")
            scored.append((t.count("\ufffd"), c, t))
    # prefer zero-replacement; tie-break by BOM/utf-8 ordering
    scored.sort(key=lambda x: (x[0], candidates.index(x[1])))
    repl, chosen, text = scored[0]
    ms = list(CHAP.finditer(text))
    chaps = []
    for m in ms:
        e = text.find("\n", m.start())
        chaps.append(text[m.start():e if e > 0 else m.start() + 60].strip())
    first_chap_pos = ms[0].start() if ms else None
    header = text[:first_chap_pos] if first_chap_pos is not None else text[:800]
    hlines = [l.strip() for l in header.splitlines() if l.strip()][:12]
    junk = {j: text.count(j) for j in JUNK if text.count(j) > 0}
    return {
        "name": name, "container_mb": round(container_bytes / 1024 / 1024, 2),
        "container_bytes": container_bytes, "blob_bytes": len(blob),
        "chars": len(text), "bom": bom, "codecs": encs, "chosen": chosen,
        "repl_chars": repl, "nchap": len(chaps),
        "chap_examples": chaps[:3], "chap_last": chaps[-2:] if chaps else [],
        "first_chapter_pos": first_chap_pos,
        "header_lines": hlines, "junk": junk, "extra": extra,
    }

results = {}
for name in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, name)
    if not os.path.isfile(p):
        continue
    low = name.lower()
    try:
        if low.endswith(".txt"):
            blob, cb = read_txt(p)
            results[name] = analyse(name, blob, cb, "plain txt")
        elif low.endswith(".rar"):
            blob, cb = read_rar(p)
            results[name] = analyse(name, blob, cb, "RAR5 -> bsdtar -> txt (NUL-stripped)")
        elif low.endswith(".tar"):
            blob, cb, mn = read_dzip_tar(p)
            results[name] = analyse(name, blob, cb, f"TAR(ustar) -> {mn} (UA->PK repaired ZIP) -> txt")
        elif low.endswith(".zip"):
            blob, cb, mn = read_zip(p)
            results[name] = analyse(name, blob, cb, f"ZIP -> {mn}")
    except Exception as e:
        results[name] = {"name": name, "error": f"{type(e).__name__}: {e}",
                         "container_bytes": os.path.getsize(p)}
    print("ok", name, flush=True)

with open(os.path.join(OUT, "full_report.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print("COMPLETE", len(results))
