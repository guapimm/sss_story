import os, io, re, json, zipfile, tarfile, subprocess, collections

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
os.makedirs(OUT, exist_ok=True)

CH = r"[0-9零一二三四五六七八九十百千万两〇]"
PATTERNS = collections.OrderedDict([
    ("di_X_zhang", rf"^[ \t　]*第[ \t　]*{CH}+[ \t　]*章"),
    ("di_X_jie",   rf"^[ \t　]*第[ \t　]*{CH}+[ \t　]*节"),
    ("di_X_hui",   rf"^[ \t　]*第[ \t　]*{CH}+[ \t　]*回"),
    ("di_X_juan",  rf"^[ \t　]*第[ \t　]*{CH}+[ \t　]*[卷部篇]"),
    ("Chapter_N",  r"^[ \t]*Chapter[ \t]+\d+"),
    ("juan_X",     rf"^[ \t　]*卷[ \t　]*{CH}+"),
    ("bracket_num", r"^[ \t　]*[【\[]\s*\d+\s*[】\]]"),
])
COMPILED = {k: re.compile(v, re.M) for k, v in PATTERNS.items()}

JUNK = ["http://", "https://", "www.", ".com", ".net", ".cn", "小说网", "笔趣阁",
        "请记住本站", "最新章节", "txt下载", "TXT下载", "手机版", "免费阅读",
        "首发", "加入书架", "更多精彩", "全文字", "无弹窗", "更新最快",
        "本文由", "整理制作", "书香门第", "派派小说", "久久小说", "顶点点",
        "www.38xs", "79文学", "天天小说网"]

# clinical adult-marker tally (counts only, never echoed)
ADULT = ["肉", "h文", "H文", "床戏", "荤", "未删", "补肉", "番外", "情色", "做爱", "勃起", "高潮"]

def decode_report(blob, label):
    """Return dict describing which codecs decode the blob cleanly."""
    res = {"len": len(blob), "bom": None, "ok": {}, "replacement": {}}
    if blob[:3] == b"\xef\xbb\xbf":
        res["bom"] = "utf-8-sig"
    elif blob[:2] in (b"\xff\xfe", b"\xfe\xff"):
        res["bom"] = "utf-16-le" if blob[:2] == b"\xff\xfe" else "utf-16-be"
    for enc in ("utf-8", "gb18030", "gbk", "utf-16", "utf-16-le", "utf-16-be", "big5"):
        try:
            s = blob.decode(enc)
            bad = s.count("\ufffd")
            res["ok"][enc] = True
            res["replacement"][enc] = bad
            # textual sanity: fraction of chars that are printable CJK/ascii/punct
            good = sum(1 for c in s if c.isprintable() or c in "\r\n\t")
            res.setdefault("printable_ratio", {})[enc] = round(good / max(1, len(s)), 4)
        except UnicodeDecodeError as e:
            res["ok"][enc] = False
            res["replacement"][enc] = None
            res.setdefault("errpos", {})[enc] = e.start
    return res

txt_files = sorted([f for f in os.listdir(ROOT) if f.lower().endswith(".txt")])
report = {}
for name in txt_files:
    p = os.path.join(ROOT, name)
    size = os.path.getsize(p)
    with open(p, "rb") as f:
        head = f.read(200_000)
        f.seek(max(0, size - 200_000))
        tail = f.read()
    whole = None
    try:
        with open(p, "rb") as f:
            whole = f.read()
    except Exception as e:
        whole = head

    enc_head = decode_report(head, name)

    # choose codec: prefer BOM, then utf-8 strict, then gb18030
    chosen = None
    if enc_head["bom"] == "utf-8-sig":
        chosen = "utf-8-sig"
    elif enc_head["bom"] in ("utf-16-le", "utf-16-be"):
        chosen = enc_head["bom"]
    elif enc_head["ok"].get("utf-8"):
        chosen = "utf-8"
    elif enc_head["ok"].get("gb18030"):
        chosen = "gb18030"
    else:
        chosen = "gb18030"

    text = whole.decode(chosen, errors="replace")
    repl = text.count("\ufffd")

    counts = {}
    examples = {}
    for k, rx in COMPILED.items():
        ms = list(rx.finditer(text))
        counts[k] = len(ms)
        ex = []
        for m in ms[:3]:
            line = text[m.start():text.find("\n", m.start()) if text.find("\n", m.start()) != -1 else m.start() + 60]
            ex.append(line.strip()[:80])
        examples[k] = ex

    # dominant chapter pattern = highest count
    dom = max(counts, key=lambda k: counts[k]) if any(counts.values()) else None

    # header block: text before first dominant chapter heading
    first_pos = None
    if dom:
        m = COMPILED[dom].search(text)
        if m:
            first_pos = m.start()
    header = text[:first_pos] if first_pos is not None else text[:1500]
    header_lines = [l.rstrip() for l in header.splitlines() if l.strip()][:14]

    junk_hits = {j: text.count(j) for j in JUNK if text.count(j) > 0}
    adult_hits = {a: text.count(a) for a in ADULT if text.count(a) > 0}

    report[name] = {
        "size_bytes": size,
        "size_mb": round(size / 1024 / 1024, 2),
        "enc_head": enc_head,
        "chosen_codec": chosen,
        "strict_decode_whole": None,
        "replacement_chars_whole": repl,
        "char_len": len(text),
        "counts": counts,
        "dominant": dom,
        "examples": examples,
        "header_lines": header_lines,
        "junk_hits": junk_hits,
        "adult_hits": adult_hits,
    }
    print("done", name, flush=True)

with open(os.path.join(OUT, "txt_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
print("TXT COMPLETE", len(report))
