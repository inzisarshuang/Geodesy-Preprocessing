#!/usr/bin/env python3
"""Real subprocess fixture for Sentinel-1 and DEM command integration tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def record(stage: str, arguments: list[str]) -> None:
    path = Path.cwd() / "invocations.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "stage": stage,
                    "argv": arguments,
                    "cwd": str(Path.cwd()),
                    "path": os.environ.get("PATH", ""),
                },
                sort_keys=True,
            )
            + "\n"
        )


def main() -> int:
    if Path(sys.argv[0]).name == "fake_srtm2dem":
        mode = os.environ.get("FAKE_SRTM_MODE", "success")
        _, output_dem, output_par, *_ = sys.argv[1:]
        if mode == "exit-1":
            print("forced srtm2dem failure", file=sys.stderr)
            return 19
        Path(output_dem).write_bytes(b"\0" * 24)
        if mode != "missing-par":
            data_format = "INTEGER*2" if mode == "bad-format" else "REAL*4"
            Path(output_par).write_text(
                "DEM_projection: EQA\n"
                f"data_format: {data_format}\n"
                "width: 3\n"
                "nlines: 2\n",
                encoding="ascii",
            )
        Path.cwd().joinpath("srtm2dem.log").write_text(
            f"fake mode={mode}\n", encoding="utf-8"
        )
        return 0
    stage, *arguments = sys.argv[1:]
    record(stage, arguments)
    if os.environ.get("FAKE_FAIL_STAGE") == stage:
        print(f"forced failure: {stage}", file=sys.stderr)
        return 17
    if stage == "safe_import":
        item = arguments[arguments.index("--root_name") + 1]
        output = Path(arguments[arguments.index("--out_dir") + 1])
        (output / f"{item}.vv.SLC_tab").write_text("tab\n", encoding="ascii")
        (output / "reader.burst_number_table").write_text("temporary\n")
        (output / "read_S1_TOPS_SLC.20260918T000000.log").write_text("temporary\n")
    elif stage == "zip_import":
        item = os.environ["FAKE_ITEM"]
        pol = "vv" if arguments[2] == "-" else arguments[2].lower()
        (Path.cwd() / f"{item}.{pol}.SLC_tab").write_text("tab\n", encoding="ascii")
    elif stage == "multi_look":
        Path(arguments[1]).write_bytes(b"mli")
        Path(arguments[2]).write_text("range_samples: 123\nazimuth_lines: 45\n", encoding="ascii")
    elif stage == "preview":
        Path(arguments[0] + ".bmp").write_bytes(b"bmp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
