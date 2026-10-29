"""Bottle creation — a port of CrossOver's ``CXBT_<template>->create()``.

CrossOver path (cx engines): write ``cxbottle.conf`` + ``system.reg`` stub,
then apply the template's ``<Winver>Install`` section of ``crossover.inf``
through ``setupapi.dll,InstallHinfSection`` — once per architecture on win64
bottles — then fix up ``dosdevices`` and wait for wineserver to settle.

Generic path (any other engine): let wine initialise the prefix itself via
``wineboot``, then record our metadata in the same ``cxbottle.conf`` format
so bottles remain CrossOver-compatible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .bottle import (
    Bottle, TEMPLATES, DEFAULT_TEMPLATE, CxConfig, bottle_roots,
    default_create_root, valid_bottle_name, write_new_bottle_conf,
    write_system_reg_stub,
)
from .engine import Engine
from .launcher import LaunchOptions, LaunchError, run_wine_app, wineserver_ctl, build_environment
from .util import new_bottle_id
from . import __version__


class CreateError(RuntimeError):
    pass


def create_bottle(name: str, engine: Engine, template: str = DEFAULT_TEMPLATE,
                  description: str | None = None, root: Path | None = None,
                  log=print) -> Bottle:
    if not valid_bottle_name(name):
        raise CreateError(f"invalid bottle name {name!r}")
    template = template.lower()
    if template not in TEMPLATES:
        # accept wine-style names like "windows 10 64-bit"
        raise CreateError(
            f"unknown template {template!r}; valid: {', '.join(TEMPLATES)}"
        )
    winver, winearch = TEMPLATES[template]
    dest_root = root or default_create_root(prefer_crossover=engine.kind == "cx")
    dest = dest_root / name
    if dest.exists() and any(dest.iterdir()):
        raise CreateError(f"{dest} already exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)

    version = engine.version or ""
    write_new_bottle_conf(dest, template=template, winearch=winearch,
                          engine_version=version,
                          timestamp=engine.build_timestamp or None,
                          decanter_version=__version__)
    cfg = CxConfig(dest / "cxbottle.conf")
    if description:
        cfg.set("Bottle", "Description", description)
        cfg.write(dest / "cxbottle.conf")
    write_system_reg_stub(dest, winearch)

    bottle = Bottle(dest, name, CxConfig(dest / "cxbottle.conf"))
    opts = LaunchOptions(bottle=bottle, engine=engine, wait=True, gui_dialogs=False)

    if engine.kind == "cx" and engine.bottle_data_dir:
        _init_via_crossover_inf(bottle, engine, winver, opts, log)
    else:
        _init_via_wineboot(bottle, engine, winver, opts, log)

    # recreate the y: drive -> $HOME exactly like CXBT::create
    home = Path.home()
    ydrive = dest / "dosdevices" / "y:"
    try:
        if ydrive.is_symlink() or ydrive.exists():
            ydrive.unlink()
        ydrive.symlink_to(home)
    except OSError as exc:
        log(f"warning: unable to create y: drive: {exc}")

    # let wineserver flush the registry to disk (works around wine bug 41713)
    wineserver_ctl(opts, "-w")
    return bottle


def _inf_section_for(winver: str) -> str:
    return {
        "win98": "Win98Install", "win2000": "Win2000Install",
        "winxp": "WinXPInstall", "vista": "VistaInstall",
        "win7": "Win7Install", "win8": "Win8Install",
        "win10": "Win10Install", "win11": "Win11Install",
    }[winver]


def _init_via_crossover_inf(bottle: Bottle, engine: Engine, winver: str,
                            opts: LaunchOptions, log) -> None:
    inf = engine.bottle_data_dir / "crossover.inf"
    section = _inf_section_for(winver)
    overrides = ";".join([
        "advpack=b", "atl=b", "oleaut32=b", "rpcrt4=b",
        "shdocvw=b", "*iexplore.exe=b",
    ])
    opts.winver = winver  # CX_WINDOWS_VERSION — what the perl wrapper does
    opts.desktop = "root"
    opts.dlloverrides = overrides
    args = [
        "setupapi.dll,InstallHinfSection", section, "128", str(inf),
    ]
    log(f"applying {section} from crossover.inf (64-bit)")
    rc = run_wine_app(opts, "rundll32.exe", args, arch="64")
    if rc:
        raise CreateError(f"rundll32 {section} failed (rc={rc})")
    has_i386 = any(d.name == "i386-windows"
                   for d in engine.dll_search_dirs(bottle.winearch))
    if bottle.winearch == "win64" and has_i386:
        log(f"applying {section} from crossover.inf (32-bit side)")
        rc = run_wine_app(opts, "rundll32.exe", args, arch="32")
        if rc:
            raise CreateError(f"32-bit rundll32 {section} failed (rc={rc})")


def _init_via_wineboot(bottle: Bottle, engine: Engine, winver: str,
                       opts: LaunchOptions, log) -> None:
    log("initialising prefix via wineboot")
    env = build_environment(opts)
    boot = None
    for cand in ("wineboot", "wineboot.exe"):
        p = engine.bin_dir / cand
        if p.is_file():
            boot = str(p)
            break
    if boot:
        rc = subprocess.call([boot, "--init"], env=env)
    else:
        # fall back to `wine wineboot` through the launcher
        from .launcher import run
        rc = run(opts, ["wineboot.exe", "--init"])
    if rc:
        raise CreateError(f"wineboot failed (rc={rc})")
    # record the requested windows version persistently
    set_windows_version(bottle, engine, winver)


# ----------------------------------------------------------------------------
# persistent registry-backed settings
# ----------------------------------------------------------------------------

def reg_add(bottle: Bottle, engine: Engine, key: str, value: str,
            data: str, vtype: str = "REG_SZ") -> int:
    opts = LaunchOptions(bottle=bottle, engine=engine, wait=True,
                         gui_dialogs=False)
    args = ["add", key, "/v", value, "/d", data, "/t", vtype, "/f"]
    rc = run_wine_app(opts, "reg.exe", args)
    if rc:
        # a fresh bottle's wineserver can still be mid-bootstrap — let it
        # settle and try once more
        wineserver_ctl(opts, "-w")
        rc = run_wine_app(opts, "reg.exe", args)
    return rc


def reg_delete(bottle: Bottle, engine: Engine, key: str, value: str) -> int:
    opts = LaunchOptions(bottle=bottle, engine=engine, wait=True,
                         gui_dialogs=False)
    return run_wine_app(opts, "reg.exe",
                        ["delete", key, "/v", value, "/f"])


def set_windows_version(bottle: Bottle, engine: Engine, winver: str) -> int:
    """Persistent Windows version: HKCU\\Software\\Wine -> Version."""
    return reg_add(bottle, engine, r"HKCU\Software\Wine", "Version", winver)


def set_dll_override(bottle: Bottle, engine: Engine, dll: str,
                     mode: str | None) -> int:
    """mode: native|builtin|native,builtin|... or None to remove."""
    if mode is None:
        opts = LaunchOptions(bottle=bottle, engine=engine, wait=True)
        return run_wine_app(opts, "reg.exe",
                            ["delete", r"HKCU\Software\Wine\DllOverrides",
                             "/v", dll, "/f"])
    return reg_add(bottle, engine, r"HKCU\Software\Wine\DllOverrides",
                   dll, mode)


def set_direct3d_key(bottle: Bottle, engine: Engine, name: str,
                     value: str | None) -> int:
    if value is None:
        opts = LaunchOptions(bottle=bottle, engine=engine, wait=True)
        return run_wine_app(opts, "reg.exe",
                            ["delete", r"HKCU\Software\Wine\Direct3D",
                             "/v", name, "/f"])
    return reg_add(bottle, engine, r"HKCU\Software\Wine\Direct3D", name, value)
