"""A small YAML writer for Kubernetes manifests (standard library only; no PyYAML)."""

from __future__ import annotations

import json
import re

PLAIN = re.compile(r"^[A-Za-z_/][A-Za-z0-9_./-]*$")
AMBIGUOUS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"}


def scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    if PLAIN.match(s) and s.lower() not in AMBIGUOUS:
        return s
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid YAML double-quoted scalar


def _lines(v, indent: int) -> list[str]:
    pad = "  " * indent
    out = []
    if isinstance(v, dict):
        for k, x in v.items():
            key = scalar(k)
            if isinstance(x, (dict, list)) and x:
                out.append(f"{pad}{key}:")
                out.extend(_lines(x, indent + (0 if isinstance(x, list) else 1)))
            else:
                out.append(f"{pad}{key}: {_empty(x)}")
    elif isinstance(v, list):
        for x in v:
            if isinstance(x, (dict, list)) and x:
                sub = _lines(x, indent + 1)
                first = sub[0].lstrip()
                out.append(f"{pad}- {first}")
                out.extend(sub[1:])
            else:
                out.append(f"{pad}- {_empty(x)}")
    else:
        out.append(pad + scalar(v))
    return out


def _empty(x) -> str:
    if isinstance(x, dict):
        return "{}"
    if isinstance(x, list):
        return "[]"
    return scalar(x)


def dump(doc) -> str:
    return "\n".join(_lines(doc, 0)) + "\n"


def dump_all(docs, header: str = "") -> str:
    body = "---\n".join(dump(d) for d in docs)
    return (header + "\n" if header else "") + body
