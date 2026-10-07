#!/usr/bin/env python3
"""Deterministic external-command fixture for Other-SAR runner tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _record(kind: str, arguments: list[str]) -> None:
    record = os.environ.get("FAKE_OTHER_SAR_RECORD")
    if not record:
        return
    with Path(record).open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {"kind": kind, "argv": arguments, "cwd": str(Path.cwd())},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def _date_from(arguments: list[str]) -> str:
    joined = "\n".join(arguments)
    for start in range(max(0, len(joined) - 7)):
        candidate = joined[start : start + 8]
        if candidate.isdigit() and candidate.startswith("20"):
            return candidate
    return "unknown"


def _write_pair(par: str, slc: str) -> None:
    Path(par).parent.mkdir(parents=True, exist_ok=True)
    Path(slc).parent.mkdir(parents=True, exist_ok=True)
    Path(par).write_text("range_samples: 4\n", encoding="ascii")
    Path(slc).write_bytes(b"slc")


def main() -> int:
    kind, *arguments = sys.argv[1:]
    _record(kind, arguments)
    date = _date_from(arguments)
    if os.environ.get("FAKE_OTHER_SAR_FAIL_ITEM") == date:
        print(f"forced failure for {date}", file=sys.stderr)
        return 23
    if os.environ.get("FAKE_OTHER_SAR_MISSING_ITEM") == date:
        return 0
    if kind == "alos":
        _write_pair(arguments[1], arguments[3])
    elif kind == "cosmo":
        trunk = arguments[1]
        _write_pair(f"{trunk}_HH_S1.slc.par", f"{trunk}_HH_S1.slc")
    elif kind == "lt1":
        _write_pair(arguments[2], arguments[3])
    elif kind == "rsat2":
        _write_pair(arguments[-2], arguments[-1])
    elif kind == "saocom":
        _write_pair(arguments[-3], arguments[-2])
    elif kind == "nisar":
        root = arguments[1]
        _write_pair(f"{root}.slc.par", f"{root}.slc")
        Path(f"{root}.slc.par").write_text(
            "range_samples: 4\nimage_format: FCOMPLEX\ncenter_frequency: 1.2575e9 Hz\n",
            encoding="ascii",
        )
    elif kind == "orbit":
        Path(arguments[1]).parent.mkdir(parents=True, exist_ok=True)
        Path(arguments[1]).write_text("range_samples: 4\nfiltered\n", encoding="ascii")
    elif kind == "preview":
        Path(arguments[0] + ".bmp").write_bytes(b"bmp")
    else:
        print(f"unknown fake command kind: {kind}", file=sys.stderr)
        return 24
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
