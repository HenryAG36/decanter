"""Open install recipes — decanter's equivalent of CrossTie.

A recipe is a JSON file in ``recipes/`` describing how to obtain and
install a Windows application: download URL, target bottle template,
installer environment, pre-install registry presets, winetricks
dependencies and optional installer arguments. The model mirrors the
observable semantics of CrossOver's c4p install profiles (download ->
bottle -> env -> registry -> run installer -> winetricks deps), with a
smaller, open schema.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import sys

from .bottle import Bottle
from .create import create_bottle, reg_add
from .engine import Engine, default_engine
from .launcher import LaunchOptions, run, run_unix
from .util import CxConfig


def eprint(*a):
    print(*a, file=sys.stderr)

RECIPE_DIRS = [
    Path(__file__).resolve().parent.parent / "recipes",
    Path.home() / ".decanter" / "recipes",
]


def _recipe_files() -> list[Path]:
    files = []
    for d in RECIPE_DIRS:
        if d.is_dir():
            files += sorted(d.glob("*.json"))
    return files


def list_recipes() -> list[dict]:
    out = []
    for f in _recipe_files():
        try:
            r = json.loads(f.read_text())
            r["id"] = f.stem
            out.append(r)
        except (OSError, json.JSONDecodeError) as exc:
            eprint(f"skipping recipe {f.name}: {exc}")
    return out


def get_recipe(rid: str) -> dict | None:
    for r in list_recipes():
        if r.get("id") == rid or r.get("name") == rid:
            return r
    return None


def _download(url: str, dest: Path) -> Path:
    eprint(f"downloading {url}")
    rc = subprocess.call(
        ["curl", "-fL", "--progress-bar", "-o", str(dest), url])
    if rc != 0 or not dest.exists() or dest.stat().st_size == 0:
        raise RuntimeError(f"download failed (curl rc={rc}): {url}")
    return dest


def _apply_registry(bottle: Bottle, engine: Engine, entries: list[dict]) -> None:
    """Apply recipe registry presets via `reg add`."""
    for e in entries:
        rc = reg_add(bottle, engine, e["key"], e["value"],
                     e["data"], e.get("type", "REG_SZ"))
        if rc != 0:
            eprint(f"warning: reg add {e['key']}\\{e['value']} failed")


def run_winetricks(bottle: Bottle, engine: Engine, verbs: list[str]) -> None:
    wt = shutil.which("winetricks")
    if wt is None:
        eprint("winetricks not installed — skipping dependency verbs "
               f"{verbs} (`brew install winetricks`)")
        return
    for v in verbs:
        eprint(f"winetricks {v}")
        run_unix(engine, bottle, [wt, "-q", v])


def install_app(rid: str, bottle_name: str | None,
                engine: Engine | None = None,
                template: str | None = None,
                dry_run: bool = False) -> Bottle | None:
    recipe = get_recipe(rid)
    if recipe is None:
        raise ValueError(f"unknown recipe {rid!r} (see `decanter install --list`)")
    eng = engine or default_engine()
    if eng is None:
        raise RuntimeError("no engine available")
    name = bottle_name or recipe.get("bottle_name") or recipe["name"]

    if dry_run:
        print(json.dumps({"recipe": recipe["name"], "bottle": name,
                          "engine": eng.name, "steps": _plan(recipe)}, indent=2))
        return None

    eprint(f"installing {recipe['name']} into bottle {name!r}")
    from .bottle import find_bottle
    existing = find_bottle(name)
    if existing is not None:
        eprint(f"bottle {name!r} already exists — installing into it")
        bottle = existing
    else:
        bottle = create_bottle(
            name, eng,
            template=template or recipe.get("template", "win10_64"),
            description=f"{recipe['name']} (decanter recipe)")

    # installer env vars live in the bottle's env section like CrossOver's
    env = recipe.get("env", {})
    if env:
        cfg = CxConfig(bottle.conf_path)
        for k, v in env.items():
            cfg.set("EnvironmentVariables", k, str(v))
        cfg.write()

    with tempfile.TemporaryDirectory(prefix="decanter-inst-") as td:
        installer = _download(recipe["url"], Path(td) / recipe["filename"])
        presets = recipe.get("registry", [])
        if presets:
            _apply_registry(bottle, eng, presets)
        if recipe["filename"].lower().endswith(".msi"):
            args = ["msiexec.exe", "/i", str(installer), *recipe.get("args", [])]
        else:
            args = [str(installer), *recipe.get("args", [])]
        eprint(f"running installer: {installer.name}")
        rc = run(LaunchOptions(bottle=bottle, engine=eng), args)
        if isinstance(rc, subprocess.CompletedProcess):
            rc = rc.returncode
        if rc != 0:
            raise RuntimeError(f"installer exited with code {rc}")

    verbs = recipe.get("winetricks", [])
    if verbs:
        run_winetricks(bottle, eng, verbs)

    post = recipe.get("post_run")
    if post:
        eprint(f"launching {post}")
        run(LaunchOptions(bottle=bottle, engine=eng), [post])
    return bottle


def _plan(recipe: dict) -> list[str]:
    steps = [f"create bottle (template {recipe.get('template', 'win10_64')})",
             f"download {recipe['url']}"]
    if recipe.get("env"):
        steps.append(f"set installer env {list(recipe['env'])}")
    if recipe.get("registry"):
        steps.append(f"apply {len(recipe['registry'])} registry presets")
    steps.append(f"run {recipe['filename']} {' '.join(recipe.get('args', []))}")
    if recipe.get("winetricks"):
        steps.append(f"winetricks: {' '.join(recipe['winetricks'])}")
    if recipe.get("post_run"):
        steps.append(f"post-run {recipe['post_run']}")
    return steps
