"""High-level per-bottle settings.

Two storage layers, same as CrossOver:

* ``cxbottle.conf`` ``[EnvironmentVariables]`` — env vars exported at launch
  (this is where CrossOver itself stores ``CX_GRAPHICS_BACKEND``,
  ``WINEMSYNC``, etc.)
* The Wine registry (``HKCU\\Software\\Wine\\...``) — persistent winver,
  DLL overrides, Direct3D / Mac Driver tweaks. Edited through wine's own
  ``reg.exe`` so nothing has to parse registry internals.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .bottle import Bottle
from .create import (
    reg_add, set_windows_version, set_dll_override, set_direct3d_key,
)
from .engine import Engine

GRAPHICS_BACKENDS = ("auto", "d3dmetal", "dxmt", "dxvk", "wined3d")
# d3d dlls each translation layer owns — used for file-deploy fallback on
# generic engines and for cleaning up when switching backends.
DXVK_DLLS = ("d3d9.dll", "d3d10.dll", "d3d10_1.dll", "d3d10core.dll",
             "d3d11.dll", "dxgi.dll", "dxvk_config.dll")
DXMT_DLLS = ("d3d10core.dll", "d3d11.dll", "dxgi.dll", "winemetal.dll")
GPTK_DLLS = ("d3d11.dll", "d3d12.dll", "dxgi.dll", "nvapi64.dll",
             "nvngx.dll", "atidxx64.dll")


def set_graphics_backend(bottle: Bottle, engine: Engine, backend: str,
                         log=print) -> None:
    """Select the Direct3D translation stack for a bottle.

    On cx engines this is exactly what CrossOver's GUI toggle does: it writes
    ``CX_GRAPHICS_BACKEND`` into the bottle's env section and the patched
    wine binaries route D3D calls accordingly.

    On generic engines there is no such dispatcher, so we do what DXVK's own
    setup scripts do: deploy the translation DLLs into system32/syswow64 and
    mark them native via DllOverrides.
    """
    backend = backend.lower()
    if backend not in GRAPHICS_BACKENDS:
        raise ValueError(f"unknown backend {backend!r}; choose from {GRAPHICS_BACKENDS}")

    if engine.kind == "cx":
        if backend == "auto":
            bottle.set_env("CX_GRAPHICS_BACKEND", None)
        else:
            if not engine.supports_backend(backend):
                log(f"warning: engine lacks {backend} payload; wine may fall back")
            bottle.set_env("CX_GRAPHICS_BACKEND",
                           None if backend == "auto" else backend)
        bottle.save_config()
        return

    # generic engine path
    if backend in ("auto", "wined3d"):
        _undeploy_translation_dlls(bottle, engine, log)
        bottle.set_env("CX_GRAPHICS_BACKEND", None)
        bottle.save_config()
        return
    if backend == "d3dmetal":
        if engine.gptk_dir:
            _deploy_dlls(bottle, engine.gptk_dir / "wine",
                         GPTK_DLLS, engine, log)
        else:
            log("this engine is not a GPTK build and has no D3DMetal payload; "
                "use `decanter engine setup-d3dmetal` first")
            return
    elif backend == "dxvk":
        if not engine.dxvk_dir:
            raise ValueError("engine has no bundled DXVK; install DXVK into the "
                             "engine or use winetricks dxvk")
        _deploy_dlls(bottle, engine.dxvk_dir, DXVK_DLLS, engine, log)
    elif backend == "dxmt":
        if not engine.dxmt_dir:
            raise ValueError("engine has no bundled DXMT")
        _deploy_dlls(bottle, engine.dxmt_dir, DXMT_DLLS, engine, log)
        # dxmt needs its unix side loaded; put it next to ntdll.so if we can
        so = engine.dxmt_dir / "x86_64-unix" / "winemetal.so"
        if so.is_file():
            for udir in engine.unix_lib_wine_dirs():
                try:
                    shutil.copy2(so, udir / so.name)
                except OSError:
                    pass
    bottle.set_env("CX_GRAPHICS_BACKEND",
                   None if backend == "auto" else backend)
    bottle.save_config()


def _sys_dirs(bottle: Bottle) -> tuple[Path, Path | None]:
    sys32 = bottle.drive_c / "windows" / "system32"
    wow64 = bottle.drive_c / "windows" / "syswow64"
    return sys32, wow64 if wow64.is_dir() else None


def _deploy_dlls(bottle: Bottle, src_root: Path, dlls: tuple[str, ...],
                 engine: Engine, log) -> None:
    sys32, wow64 = _sys_dirs(bottle)
    deployed: list[str] = []
    for dll in dlls:
        x64 = src_root / "x86_64-windows" / dll
        x32 = src_root / "i386-windows" / dll
        if x64.is_file():
            shutil.copy2(x64, sys32 / dll)
            deployed.append(dll)
        if x32.is_file() and wow64:
            shutil.copy2(x32, wow64 / dll)
    for dll in deployed:
        name = dll[:-4]
        set_dll_override(bottle, engine, name, "native")
    log(f"deployed {len(deployed)} translation dlls into the bottle")


def _undeploy_translation_dlls(bottle: Bottle, engine: Engine, log) -> None:
    """Restore wine-builtin d3d dlls and drop their native overrides."""
    sys32, wow64 = _sys_dirs(bottle)
    for dll in set(DXVK_DLLS + DXMT_DLLS + GPTK_DLLS):
        for target_dir, archdir in ((sys32, "x86_64-windows"),
                                    (wow64, "i386-windows")):
            if target_dir is None:
                continue
            src = None
            for d in engine.lib_dirs:
                cand = d / "wine" / archdir / dll
                if cand.is_file():
                    src = cand
                    break
            dst = target_dir / dll
            if src and dst.exists():
                shutil.copy2(src, dst)
        name = dll[:-4]
        set_dll_override(bottle, engine, name, None)


def set_sync(bottle: Bottle, kind: str, enabled: bool) -> None:
    """esync/msync/fsync — env-var toggles like CrossOver's."""
    key = {"msync": "WINEMSYNC", "esync": "WINEESYNC",
           "fsync": "WINEFSYNC"}.get(kind)
    if key is None:
        raise ValueError(f"unknown sync mechanism {kind!r}")
    bottle.set_env(key, "1" if enabled else "0")
    bottle.save_config()


def set_retina(bottle: Bottle, engine: Engine, enabled: bool) -> int:
    return reg_add(bottle, engine, r"HKCU\Software\Wine\Mac Driver",
                   "RetinaMode", "y" if enabled else "n")


def set_dxvk_hud(bottle: Bottle, value: str | None) -> None:
    bottle.set_env("DXVK_HUD", value)
    bottle.save_config()


def apply_config(bottle: Bottle, engine: Engine, key: str, value: str,
                 log=print) -> None:
    """Map ``decanter config set KEY=VALUE`` names to actions."""
    k = key.lower()
    if k in ("backend", "graphics", "graphics_backend"):
        set_graphics_backend(bottle, engine, value or "auto", log)
    elif k == "msync":
        set_sync(bottle, "msync", _truthy(value))
    elif k == "esync":
        set_sync(bottle, "esync", _truthy(value))
    elif k == "fsync":
        set_sync(bottle, "fsync", _truthy(value))
    elif k in ("winver", "windows_version"):
        rc = set_windows_version(bottle, engine, value)
        if rc:
            raise RuntimeError(f"reg add failed (rc={rc})")
        bottle.config.set("Bottle", "WindowsVersion", value)
        bottle.save_config()
    elif k == "retina":
        set_retina(bottle, engine, _truthy(value))
    elif k in ("dxvk_hud", "hud"):
        set_dxvk_hud(bottle, value or None)
    elif k.startswith("override:"):
        dll = key.split(":", 1)[1]
        set_dll_override(bottle, engine, dll, value or None)
    elif k.startswith("env:"):
        var = key.split(":", 1)[1]
        bottle.set_env(var, value or None)
        bottle.save_config()
    else:
        # fall through: treat bare KEY as an environment variable
        bottle.set_env(key, value or None)
        bottle.save_config()


def _truthy(v: str) -> bool:
    return v.lower() in ("1", "true", "yes", "on", "y")


KNOWN_KEYS = [
    "backend  — d3dmetal|dxmt|dxvk|wined3d|auto (graphics translation stack)",
    "msync   — mach-port sync primitives (CrossOver default on)",
    "esync/fsync — eventfd/futex sync (generic wine)",
    "winver  — reported Windows version (win11, win10, win7, ...)",
    "retina  — Retina/HiDPI rendering in the Mac driver",
    "dxvk_hud — DXVK overlay ('devinfo,fps' etc)",
    "override:<dll> — DLL override mode (native, builtin, native,builtin, disabled)",
    "env:<VAR> — arbitrary per-bottle environment variable",
]
