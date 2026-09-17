"""测 Hindsight retain 的**边际成本** —— 决定"能否批量灌正文"的唯一数字。

## 为什么要测

实验 C 显示 retain **1 条短规则**花了 3,304 tokens（input 2,998）。
但那个 input 很可能主要是 **fact-extraction 的固定 prompt 开销**，
而不是内容本身。**必须区分固定开销与边际成本**：

  · 若边际成本低（比如 ~1x 内容长度）→ 批量索引在成本上可行
  · 若边际成本高（比如 3~5x 内容长度）→ 批量索引不可行

这本项目 P2 已实测「输出占成本 77.5%」，所以**输出 token 是关键**。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8888"
BANK = "storyforge::cost-probe"

#: 三段递增内容：验证 input/output 随内容长度的增长
SAMPLES: list[tuple[str, str]] = [
    ("短(约100字)", "【规范】章节标题必须具体且有信息量，禁止与其他章重复。" * 1),
    ("中(约500字)", "【规范】章节标题必须具体且有信息量，禁止与其他章重复。" * 5),
    ("长(约2500字)", "【规范】章节标题必须具体且有信息量，禁止与其他章重复。" * 25),
]


def _post(path: str, payload: dict, timeout: int = 180) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}", data=data,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _delete(path: str) -> None:
    req = urllib.request.Request(f"{BASE}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"  （清理 {path} 失败：{exc}）")


def main() -> int:
    print("=" * 82)
    print("Hindsight retain 边际成本实测")
    print("=" * 82)

    rows: list[tuple[str, int, int, int, int, float]] = []
    for label, content in SAMPLES:
        try:
            t0 = time.monotonic()
            r = _post(f"/v1/default/banks/{BANK}/memories",
                      {"items": [{"content": content}]})
            dt = time.monotonic() - t0
            u = r.get("usage") or {}
            rows.append((
                label, len(content),
                int(u.get("input_tokens") or 0),
                int(u.get("output_tokens") or 0),
                int(u.get("total_tokens") or 0),
                dt,
            ))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            print(f"  {label}: HTTP {exc.code} — {body}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {label}: {type(exc).__name__}: {exc}")

    print()
    print(f"{'样本':<16}{'内容字符':>9}{'input':>9}{'output':>8}{'total':>8}"
          f"{'秒':>7}{'total/字符':>11}")
    for label, n_chars, tin, tout, tot, dt in rows:
        ratio = tot / n_chars if n_chars else 0
        print(f"{label:<16}{n_chars:>9}{tin:>9}{tout:>8}{tot:>8}"
              f"{dt:>7.1f}{ratio:>11.2f}")

    if len(rows) >= 2:
        print()
        print("=" * 82)
        print("固定开销 vs 边际成本（用最小二乘拟合 total = a + b × 字符数）")
        print("=" * 82)
        n = len(rows)
        xs = [r[1] for r in rows]
        ys = [r[4] for r in rows]
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denom = n * sxx - sx * sx
        if denom:
            b = (n * sxy - sx * sy) / denom
            a = (sy - b * sx) / n
            print(f"  固定开销 a ≈ {a:,.0f} tokens / 次调用")
            print(f"  边际成本 b ≈ {b:.2f} tokens / 字符")
            print()
            print("  换算：")
            print(f"    一章 5,000 字  → 约 {a + b * 5000:,.0f} tokens")
            print(f"    一本 1,500 章  → 约 {(a + b * 5000) * 1500:,.0f} tokens")
            print(f"    语料 266 MB（约 1.3 亿字） → 约 "
                  f"{(a + b * 5000) * 26000:,.0f} tokens")
        else:
            print("  样本不足，无法拟合")

    print()
    print("=" * 82)
    print("清理探针 bank")
    print("=" * 82)
    _delete(f"/v1/default/banks/{BANK}")
    print("  已删除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
