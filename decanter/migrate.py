"""One-command migration off CrossOver: move bottles into decanter's own
bottle root and make sure a working open engine is installed.

Nothing is deleted — bottles are *moved* (not copied) from
``~/Library/Application Support/CrossOver/Bottles`` into
``~/.decanter/bottles`` so they survive the user removing CrossOver and
its support folder. Bottle environment is retuned to what the open
engine actually supports.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .bottle import (CROSSOVER_USER_DIR, Bottle, find_bottle,
                     get_default_bottle, list_bottles, set_default_bottle)
from .engine import Engine, discover_engines, fetch_open_engine_releases, install_engine
from .util import CxConfig

DECANTER_BOTTLES = Path.home() / ".decanter" / "bottles"


def eprint(*a):
    print(*a, file=sys.stderr)


def _pick_engine_asset(releases: list[dict]) -> dict | None:
    """Prefer winecx builds (newer wine + DXVK + MoltenVK bundled)."""
    assets = [a for r in releases for a in r["assets"]]
    for pat in ("winecx", "WineCX", "wine-crossover"):
        for a in assets:
            if pat.lower() in a["name"].lower():
                return a
    return assets[0] if assets else None


def _retune_bottle(bottle: Bottle, engine: Engine, dry: bool, log) -> None:
    """Rewrite per-bottle env so it doesn't reference CrossOver-only
    capabilities (d3dmetal/dxmt/msync) the open engine lacks."""
    backends = set(engine.available_backends())
    cfg = CxConfig(bottle.conf_path)
    env = bottle.env_vars()
    changed = False
    backend = env.get("CX_GRAPHICS_BACKEND")
    if backend and backend not in backends:
        log(f"  {bottle.name}: backend {backend} not supported by "
            f"{engine.name} -> auto")
        if not dry:
            cfg.unset("EnvironmentVariables", "CX_GRAPHICS_BACKEND")
        changed = True
    if env.get("WINEMSYNC") == "1" and engine.kind != "cx":
        log(f"  {bottle.name}: msync is CrossOver-only -> esync")
        if not dry:
            cfg.unset("EnvironmentVariables", "WINEMSYNC")
            cfg.set("EnvironmentVariables", "WINEESYNC", "1")
        changed = True
    if changed and not dry:
        cfg.write()


def migrate(dry_run: bool = False, engine_url: str | None = None,
            progress=eprint) -> dict:
    steps: list[str] = []

    # 1. ensure an open engine -------------------------------------------
    engines = discover_engines()
    open_engines = [e for e in engines if e.kind != "cx"]
    target: Engine | None = open_engines[0] if open_engines else None
    if target is None:
        if engine_url is None:
            progress("no open engine installed — finding one")
            asset = _pick_engine_asset(fetch_open_engine_releases())
            if asset is None:
                raise RuntimeError("could not find a downloadable open engine")
            engine_url = asset["url"]
            progress(f"selected {asset['name']}")
        steps.append(f"install open engine from {engine_url}")
        if not dry_run:
            install_engine(engine_url, progress=progress)
            engines = discover_engines()
            open_engines = [e for e in engines if e.kind != "cx"]
            if not open_engines:
                raise RuntimeError("engine installed but not recognised")
            target = open_engines[0]
    assert target is not None
    steps.append(f"engine: {target.name} ({target.kind})")

    # 2. move bottles out of the CrossOver support folder ----------------
    cx_bottle_dir = CROSSOVER_USER_DIR / "Bottles"
    moved: list[str] = []
    default_name = None
    db = get_default_bottle()
    if db is not None:
        default_name = db.name
    for b in list_bottles():
        if cx_bottle_dir not in b.path.parents:
            continue  # already outside CrossOver's folder
        dest = DECANTER_BOTTLES / b.name
        if dest.exists():
            progress(f"  skipping {b.name}: {dest} already exists")
            continue
        steps.append(f"move bottle {b.name!r} -> {dest}")
        moved.append(b.name)
        if not dry_run:
            DECANTER_BOTTLES.mkdir(parents=True, exist_ok=True)
            shutil.move(b.path, dest)

    # 3. retune moved (and other non-cx-root) bottles --------------------
    for b in list_bottles():
        _retune_bottle(b, target, dry_run, progress)

    # 4. restore default -------------------------------------------------
    if default_name and not dry_run:
        nb = find_bottle(default_name)
        if nb is not None:
            set_default_bottle(nb)

    result = {"engine": target.name, "moved_bottles": moved,
              "decanter_bottles": str(DECANTER_BOTTLES)}
    for s in steps:
        progress(("would " if dry_run else "") + s)
    if dry_run:
        return result

    progress("")
    progress("migration complete. Safe to remove when ready:")
    progress("  - CrossOver.app")
    progress(f"  - {CROSSOVER_USER_DIR} (settings/caches; bottles were moved out)")
    progress("  - ~/Applications/CrossOver/*.app launchers (recreate with "
             "`decanter shortcut`)")
    return result
