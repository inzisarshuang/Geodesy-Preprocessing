"""Isolated Sentinel-1 TOPS SLC tab views for GAMMA consumers.

功能概述:
    校验三列 SLC tab，将大体积栅格以软链接放入任务目录，并复制 GAMMA
    可能原地更新的参数文件，避免 S1-04/S1-05 写穿透源 SLC。

函数说明:
    ``read_slc_tab_rows`` 解析并校验 tab；``stage_slc_tabs`` 创建本地 tab
    和对应的隔离输入视图。
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Sequence
from pathlib import Path


SlcTabRow = tuple[Path, Path, Path]
SlcTabSpec = tuple[Path, tuple[SlcTabRow, ...]]


def read_slc_tab_rows(
    tab_file: str | Path, scene_dir: str | Path
) -> tuple[SlcTabRow, ...]:
    """Return validated three-column rows whose files stay inside one scene."""

    scene = Path(scene_dir).resolve(strict=True)
    tab = Path(tab_file).resolve(strict=True)
    if not tab.is_file() or not tab.is_relative_to(scene):
        raise ValueError(f"SLC tab must be a file inside {scene}: {tab}")
    rows: list[SlcTabRow] = []
    for line_number, line in enumerate(tab.read_text(encoding="utf-8").splitlines(), 1):
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 3:
            raise ValueError(f"{tab}:{line_number}: SLC tab row must have three columns")
        resolved: list[Path] = []
        for field in fields:
            candidate = Path(field).expanduser()
            source = (
                candidate if candidate.is_absolute() else tab.parent / candidate
            ).resolve(strict=True)
            if not source.is_file() or not source.is_relative_to(scene):
                raise ValueError(f"SLC tab source must be a file inside {scene}: {source}")
            resolved.append(source)
        rows.append((resolved[0], resolved[1], resolved[2]))
    if not rows:
        raise ValueError(f"SLC tab is empty: {tab}")
    return tuple(rows)


def stage_slc_tabs(
    specs: Sequence[SlcTabSpec], destination_dir: str | Path
) -> tuple[Path, ...]:
    """Create local GAMMA tabs with copied metadata and linked raster payloads."""

    destination = Path(destination_dir).resolve(strict=True)
    if not destination.is_dir():
        raise ValueError(f"SLC view destination must be a directory: {destination}")
    staged: dict[str, Path] = {}
    tabs: list[Path] = []
    for source_tab, rows in specs:
        for row in rows:
            for source in row:
                previous = staged.get(source.name)
                if previous is not None and previous != source:
                    raise FileExistsError(
                        f"SLC view filename collision: {source.name}: "
                        f"{previous} and {source}"
                    )
                if previous is not None:
                    continue
                target = destination / source.name
                if source.name.endswith((".par", ".tops_par")):
                    shutil.copy2(source, target)
                    target.chmod(target.stat().st_mode | stat.S_IWUSR)
                else:
                    os.symlink(source, target)
                staged[source.name] = source
        local_tab = destination / source_tab.name
        if local_tab.exists():
            raise FileExistsError(f"SLC view tab collision: {local_tab.name}")
        local_tab.write_text(
            "".join(" ".join(path.name for path in row) + "\n" for row in rows),
            encoding="utf-8",
        )
        tabs.append(local_tab)
    return tuple(tabs)


__all__ = ["SlcTabRow", "SlcTabSpec", "read_slc_tab_rows", "stage_slc_tabs"]
