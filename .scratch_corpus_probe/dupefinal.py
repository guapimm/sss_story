import os, re, io, json, zipfile, tarfile, subprocess, hashlib

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
TAR = r"C:\windows\system32\tar.exe"
L = []

def dec(b):
    if b[:3] == b"\xef\xbb\xbf":
        return b.decode("utf-8-sig")
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16")
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("gb18030")

def blob_of(name):
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
    raise ValueError(name)

PAIRS = [("剑来.rar", "《剑来》（网络版精校全本）.txt"),
         ("大奉打更人.rar", "大奉打更人.txt"),
         ("《尾钩》作者：塞纳左岸（larivegauche）.txt", "《尾钩》（实体书番外）.tar"),
         ("《欲言难止》作者：麦香鸡呢.txt", "《欲言难止》by麦香鸡呢 未删 实体.tar"),
         ("完结｜《屏香记》作者：小狐狸的大宝贝.zip", "完结｜《溺酒》作者：口卡.txt")]

P = re.compile(r"^[ \t\u3000]*(?:[☆★◇◆]\s*[、.]?\s*)?(?:\d{1,5}\s*[、.．]?\s*)?第\s*[0-9零一二三四五六七八九十百千万两〇]+\s*[章回节]", re.M)

L.append("## 重复对：内容级对照（规范化空白后比较）\n")
for a, b in PAIRS:
    L.append("=" * 78)
    for nm in (a, b):
        t = dec(blob_of(nm))
        norm = re.sub(r"\s+", "", t)
        h = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]
        ch = len(P.findall(t))
        L.append(f"  {nm}")
        L.append(f"     容器={os.path.getsize(os.path.join(ROOT,nm))} B ({round(os.path.getsize(os.path.join(ROOT,nm))/1024/1024,2)} MB)"
                 f"  正文字符={len(t)}  去空白字符={len(norm)}  章={ch}")
        L.append(f"     规范化SHA1[:16]={h}")
    # overlap metric
    ta = re.sub(r"\s+", "", dec(blob_of(a)))
    tb = re.sub(r"\s+", "", dec(blob_of(b)))
    common = sum(1 for x, y in zip(ta, tb) if x == y)
    L.append(f"  -> 等长前缀逐字符一致率: {common}/{min(len(ta),len(tb))} = "
             f"{common/max(1,min(len(ta),len(tb)))*100:.1f}%  长度比 {len(ta)}/{len(tb)}")
    same = hashlib.sha1(ta.encode()).hexdigest() == hashlib.sha1(tb.encode()).hexdigest()
    L.append(f"  -> 规范化后完全相同: {same}")
    L.append("")

# content markers - factual counts
L.append("\n## 内容风险标记（仅统计词频，不引用原文）\n")
MARK = ["肉", "未删", "补肉", "补车", "删减", "床戏", "情色", "做爱", "性器", "高潮",
        "舔", "勃起", "淫", "呻吟", "裸", "脱光", "ABO", "信息素", "发情", "骨科", "强制爱", "囚禁"]
for nm in sorted(os.listdir(ROOT)):
    p = os.path.join(ROOT, nm)
    if not os.path.isfile(p) or not nm.lower().endswith(".txt"):
        continue
    t = dec(blob_of(nm))
    hits = {m: t.count(m) for m in MARK if t.count(m) > 0}
    top = sorted(hits.items(), key=lambda kv: -kv[1])[:8]
    if top:
        L.append(f"  {nm}: {top}")

L.append("\n## 文件名中的显式标记（原文照录）\n")
for nm in sorted(os.listdir(ROOT)):
    for kw in ("未删", "补肉", "补车", "删减", "实体", "番外", "解压码", "补番"):
        if kw in nm:
            L.append(f"  [{kw}] {nm}")
            break

with open(os.path.join(OUT, "dupefinal.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
