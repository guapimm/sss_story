# -*- coding: utf-8 -*-
"""Scan a Python codebase for string-literal prompt payloads and report sizes.

Read-only: opens files for reading only.
"""
import io, os, re, sys, json

ROOT = r"E:\AI_project\story_llm\backend"

TRIPLE = re.compile(r'("""|\'\'\')(.*?)\1', re.S)
ASSIGN_TRIPLE = re.compile(r'([A-Za-z_][A-Za-z0-9_\.]*)\s*(?::[^=]+)?=\s*("""|\'\'\')', re.S)

CJK = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')


def est_tokens(s: str) -> int:
    """Rough token estimate. Chinese ~1 token/char for most tokenizers (slightly <1);
    ASCII ~1 token / 4 chars. Blend."""
    if not s:
        return 0
    cjk = len(CJK.findall(s))
    other = len(s) - cjk
    return int(cjk * 1.0 + other / 3.5)


def literals(path):
    src = io.open(path, encoding="utf-8", errors="replace").read()
    out = []
    for m in TRIPLE.finditer(src):
        body = m.group(2)
        line = src[:m.start()].count("\n") + 1
        # name = the nearest preceding assignment target on the same logical area
        head = src[max(0, m.start() - 200):m.start()]
        nm = None
        mm = list(re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=\n]+)?=\s*$', head))
        if mm:
            nm = mm[-1].group(1)
        out.append(dict(line=line, name=nm or "?", chars=len(body),
                        tokens=est_tokens(body), body=body))
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "summary"
    targets = sys.argv[2:] if len(sys.argv) > 2 else None
    outpath = None
    if mode == "json" and targets:
        outpath = targets.pop()
    rows = []
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in ("__pycache__", ".pytest_cache", "_pylibs", "node_modules", "alembic")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, ROOT)
            if targets and not any(t.replace("/", os.sep) in rel for t in targets):
                continue
            try:
                for lit in literals(p):
                    lit["file"] = rel
                    rows.append(lit)
            except Exception as e:
                print("ERR", rel, e, file=sys.stderr)

    rows.sort(key=lambda r: -r["chars"])
    if mode == "summary":
        print(f"TOTAL string literals >=0 chars: {len(rows)}")
        big = [r for r in rows if r["chars"] >= 300]
        print(f"literals >=300 chars: {len(big)}  total chars={sum(r['chars'] for r in big):,}")
        print(f"literals >=1000 chars: {len([r for r in rows if r['chars']>=1000])}")
        print(f"ALL literal chars (>=50): {sum(r['chars'] for r in rows if r['chars']>=50):,}")
        print()
        print(f"{'chars':>8} {'~tok':>7}  file:line  name")
        for r in rows[:120]:
            if r["chars"] < 300:
                break
            print(f"{r['chars']:8d} {r['tokens']:7d}  {r['file']}:{r['line']}  {r['name']}")
    elif mode == "perfile":
        agg = {}
        for r in rows:
            if r["chars"] < 300:
                continue
            a = agg.setdefault(r["file"], [0, 0])
            a[0] += 1
            a[1] += r["chars"]
        for f, (n, c) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
            print(f"{c:9,d} chars  {n:4d} blocks  {f}")
    elif mode == "json":
        json.dump(rows, io.open(outpath, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    elif mode == "show":
        pat = sys.argv[2]
        for r in rows:
            if re.search(pat, r["file"]) and r["chars"] >= 200:
                print("=" * 100)
                print(f"{r['file']}:{r['line']}  name={r['name']}  chars={r['chars']} ~tok={r['tokens']}")
                print("-" * 100)
                print(r["body"])


if __name__ == "__main__":
    main()
