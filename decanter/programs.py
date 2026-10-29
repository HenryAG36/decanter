"""Enumerate installed programs in a bottle — the same places CrossOver's
icon grid reads: ``desktopdata/cxmenu`` launcher files (Start Menu/Desktop
entries it captured), falling back to scanning drive_c for executables."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .bottle import Bottle
from .util import decode_cxmenu_name, lnk_target, parse_url_file, unix_to_win_path


def _wrapper_target(bottle: Bottle, wrapper: Path) -> str | None:
    """Resolve a cxmenu shell wrapper to its Windows-side target.

    The wrapper's ``--start "C:/..."`` argument points at the real .lnk/.url
    inside drive_c; resolve that file and extract its target.
    """
    try:
        text = wrapper.read_text(errors="replace")
    except OSError:
        return None
    m = re.search(r'--start\s+"([^"]+)"', text) or \
        re.search(r'--cx-app\s+"([^"]+)"', text)
    if not m:
        return None
    from .util import win_to_unix_path
    real = win_to_unix_path(bottle.path, m.group(1))
    if real is None or not real.is_file():
        return None
    if real.suffix.lower() == ".url":
        return parse_url_file(real)
    if real.suffix.lower() == ".lnk":
        t = lnk_target(real)
        if t:
            return t
        from .util import unix_to_win_path
        return unix_to_win_path(str(real))
    return None


def list_programs(bottle: Bottle) -> list[dict]:
    """Return [{name, target, source, icon?}] deduplicated by name."""
    programs: dict[str, dict] = {}

    cxmenu = bottle.path / "desktopdata" / "cxmenu"
    if cxmenu.is_dir():
        for f in sorted(cxmenu.rglob("*")):
            if not f.is_file():
                continue
            low = f.name.lower()
            # cxmenu dir names encode the originating windows dir; the leaf
            # name is the display name (+ = space, ^XX escapes). The files
            # are /bin/sh wrappers: exec wine ... --start "C:/path/real.lnk"
            if low.endswith((".lnk", ".url")):
                name = decode_cxmenu_name(f.name.rsplit(".", 1)[0])
                programs.setdefault(name, {
                    "name": name,
                    "target": _wrapper_target(bottle, f),
                    "source": "startmenu" if low.endswith(".lnk") else "url",
                    "link_file": str(f)})

    # also scan the real start menu + desktop inside drive_c (covers bottles
    # whose desktopdata wasn't populated, and generic prefixes)
    drive_c = bottle.drive_c
    for sub in ("users/Public/Desktop",):
        d = drive_c / sub
        if d.is_dir():
            for f in d.iterdir():
                if f.suffix.lower() == ".lnk" and f.is_file():
                    name = f.stem
                    programs.setdefault(name, {
                        "name": name, "target": lnk_target(f),
                        "source": "desktop", "link_file": str(f)})
    users = drive_c / "users"
    if users.is_dir():
        for udir in users.iterdir():
            sm = udir / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
            if sm.is_dir():
                for f in sm.rglob("*.lnk"):
                    name = f.stem
                    programs.setdefault(name, {
                        "name": name, "target": lnk_target(f),
                        "source": "startmenu", "link_file": str(f)})

    # if a .lnk didn't yield a target, try to resolve the same-named exe
    for p in programs.values():
        if not p["target"]:
            from .util import find_exe_in_drive_c
            exe = find_exe_in_drive_c(bottle.path, p["name"])
            if exe:
                p["target"] = unix_to_win_path(str(exe))
    return sorted(programs.values(), key=lambda p: p["name"].lower())
