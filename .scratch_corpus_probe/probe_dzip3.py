import os, io, struct, tarfile, zipfile

ROOT = r"E:\AI_project\story_commandcode\wangwenxiaoshuo"
OUT = r"E:\AI_project\story_commandcode\.scratch_corpus_probe"
L = []

p = os.path.join(ROOT, "《尾钩》（实体书番外）.tar")
with tarfile.open(p, "r:*") as t:
    m = t.getmembers()[0]
    blob = t.extractfile(m).read()

L.append(f"dzip size = {len(blob)}")
L.append(f"first 64 bytes: {blob[:64].hex(' ')}")
L.append(f"last 64 bytes : {blob[-64:].hex(' ')}")
L.append("")

# find all signature-like markers
for sig in (b"UA\x03\x04", b"UA\x01\x02", b"UA\x05\x06", b"UA\x07\x08",
            b"PK\x03\x04", b"PK\x01\x02", b"PK\x05\x06", b"PK\x07\x08"):
    idxs = []
    start = 0
    while True:
        i = blob.find(sig, start)
        if i < 0:
            break
        idxs.append(i)
        start = i + 1
    L.append(f"marker {sig!r}: count={len(idxs)} positions={idxs[:10]}")
L.append("")

# parse first local header as-if ZIP (30 byte header)
h = blob[:30]
fields = struct.unpack("<IHHHHHIIIHH", h)
names = ["sig", "version", "flags", "method", "mtime", "mdate", "crc32", "csize", "usize", "fnlen", "extralen"]
for n, v in zip(names, fields):
    L.append(f"  local header {n:9} = {v} (0x{v:x})" if isinstance(v, int) else f"  {n}={v}")
L.append("")
fnlen = fields[9]
extra = fields[10]
L.append(f"  -> filename bytes = {blob[30:30+fnlen]!r}")
L.append(f"  -> filename decoded utf-8 = {blob[30:30+fnlen].decode('utf-8', 'replace')!r}")
L.append(f"  -> extra bytes = {blob[30+fnlen:30+fnlen+extra].hex(' ')}")
data_start = 30 + fnlen + extra
L.append(f"  -> data starts at {data_start}")
L.append(f"  -> data first 32: {blob[data_start:data_start+32].hex(' ')}")

# try raw deflate decompress from data_start
import zlib
for wbits, label in ((-15, "raw deflate"), (15, "zlib"), (47, "gzip")):
    try:
        d = zlib.decompressobj(wbits)
        out = d.decompress(blob[data_start:data_start+2_000_000])
        L.append(f"  raw-decompress [{label}]: OK, {len(out)} bytes, used={len(blob[data_start:])-len(d.unused_data)}")
        L.append(f"     head: {out[:120].hex(' ')}")
        for enc in ("utf-8", "gb18030", "utf-16"):
            try:
                L.append(f"     decode {enc}: {out[:150].decode(enc)!r}")
            except Exception as e:
                L.append(f"     decode {enc}: FAIL {type(e).__name__}")
        break
    except Exception as e:
        L.append(f"  raw-decompress [{label}]: FAIL {type(e).__name__}: {e}")

with open(os.path.join(OUT, "dzip_struct.txt"), "w", encoding="utf-8") as f:
    f.write("\n".join(L))
print("ok")
