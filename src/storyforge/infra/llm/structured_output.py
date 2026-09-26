"""Mechanical JSON cleanup and compact schema-error feedback."""

from __future__ import annotations

import json


_BARE_CONTROL_IN_STRING = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _repair_json(s: str) -> str:
    """Escape bare control characters inside JSON strings without changing structure."""
    out: list[str] = []
    in_string = escaped = False
    for ch in s:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\":
            out.append(ch)
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
        out.append(_BARE_CONTROL_IN_STRING[ch] if in_string and ch in _BARE_CONTROL_IN_STRING else ch)
    return "".join(out)


def extract_json(text: str) -> str:
    """Extract a JSON object/array and repair only unambiguous string controls."""
    s = (text or "").strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    s = s.strip()
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j != -1 and j > i:
        s = s[i : j + 1]
    else:
        i, j = s.find("["), s.rfind("]")
        if i != -1 and j != -1 and j > i:
            s = s[i : j + 1]
    try:
        json.loads(s)
        return s
    except (ValueError, TypeError):
        return _repair_json(s)


def _summarize_validation_error(exc: Exception | None) -> str:
    """Compress schema failures into field-level repair guidance."""
    if exc is None:
        return "（无错误详情）"
    errors = getattr(exc, "errors", None)
    if not callable(errors):
        return str(exc)[:400]
    try:
        items = errors()
    except Exception:  # noqa: BLE001
        return str(exc)[:400]
    lines: list[str] = []
    for e in items[:12]:
        loc = ".".join(str(x) for x in e.get("loc", ()))
        msg = str(e.get("msg", ""))[:120]
        etype = e.get("type", "")
        if etype == "missing":
            lines.append(f"- 缺必填字段：`{loc}`")
        elif etype == "extra_forbidden":
            lines.append(f"- 多出未定义字段：`{loc}`（请删除）")
        else:
            lines.append(f"- `{loc}`：{msg}")
    if len(items) > 12:
        lines.append(f"- …另有 {len(items) - 12} 处同类问题")
    return "\n".join(lines) if lines else str(exc)[:400]
