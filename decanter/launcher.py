"""Launch Windows programs in a bottle — a Python port of CrossOver's
``bin/wine`` perl wrapper's environment contract.

Everything the patched wine binaries need is provided through the
environment; reproducing it faithfully is what keeps compatibility identical
to CrossOver itself:

* ``WINEPREFIX`` / ``CX_BOTTLE``       — which bottle
* ``WINEDLLPATH``                    — builtin-PE search path (arch ordered)
* ``WINELOADER`` / ``WINESERVER`` / ``WINEWRAPPER``
* ``CX_APPLEGPTK_LIBD3DSHARED_PATH`` — D3DMetal shared lib, when present
* ``ROSETTA_ADVERTISE_AVX=1``        — expose AVX to games under Rosetta
* ``DOTNET_EnableWriteXorExecute=0`` — .NET 7/8 fix under Rosetta
* ``GST_*``                          — gstreamer (mfplay videos)
* ``[EnvironmentVariables]``         — per-bottle env (graphics backend,
                                       msync, user additions)
"""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .bottle import Bottle, CROSSOVER_USER_DIR
from .engine import Engine
from .util import expand_env, find_exe_in_drive_c, is_win_path, unix_to_win_path


class LaunchError(RuntimeError):
    pass


@dataclass
class LaunchOptions:
    bottle: Bottle
    engine: Engine
    convert_paths: bool = True
    quote_args: bool = True
    workdir: str | None = None
    desktop: str | None = None
    winver: str | None = None
    dlloverrides: str | None = None
    extra_env: str | None = None         # "K=V K=V" shell-style
    debugmsg: str | None = None
    new_console: bool = False
    wait: bool = True
    wait_children: bool = False
    start_document: str | None = None    # `decanter start` target
    gui_dialogs: bool = False
    verbose: bool = False


def _prepend_env_path(env: dict[str, str], name: str, prefix: str) -> None:
    cur = env.get(name, "")
    if not cur:
        env[name] = prefix
    elif not (cur == prefix or cur.startswith(prefix + ":")):
        env[name] = f"{prefix}:{cur}"


def build_environment(opts: LaunchOptions) -> dict[str, str]:
    """Reproduce the environment CrossOver's perl wrapper builds."""
    env = dict(os.environ)
    eng, bottle = opts.engine, opts.bottle
    is_cx = eng.kind == "cx"

    # ---- bottle identity --------------------------------------------------
    env["CX_BOTTLE"] = bottle.name
    env["WINEPREFIX"] = str(bottle.path.resolve())
    if is_cx:
        env["CX_ROOT"] = str(eng.root)
        env["CX_HOME"] = str(CROSSOVER_USER_DIR)
        CROSSOVER_USER_DIR.mkdir(parents=True, exist_ok=True)
        env["CX_INITIALIZED"] = f"{os.getuid()}:{bottle.name}"

    # ---- wine paths -------------------------------------------------------
    _prepend_env_path(env, "PATH", str(eng.bin_dir))
    dll_dirs = eng.dll_search_dirs(bottle.winearch)
    if dll_dirs:
        env["WINEDLLPATH"] = ":".join(str(d) for d in dll_dirs)
    if eng.wineserver:
        env["WINESERVER"] = str(eng.wineserver)
    if eng.wine_loader and eng.wine_loader != eng.wine_bin:
        env["WINELOADER"] = str(eng.wine_loader)
        env["CX_WINELOADER"] = str(eng.wine_loader)
    if eng.winewrapper:
        env["WINEWRAPPER"] = str(eng.winewrapper)
    if not is_cx:
        # generic engines need their bundled dylibs (MoltenVK etc.) findable —
        # the equivalent of CrossOver's wrapper setting the dyld search path
        for d in eng.lib_dirs:
            _prepend_env_path(env, "DYLD_FALLBACK_LIBRARY_PATH", str(d))

    # ---- host quirks ------------------------------------------------------
    if platform.machine() == "arm64":
        # Rosetta doesn't treat "0" as false — must unset entirely. Only
        # CrossOver's own wine tolerates ROSETTA_ADVERTISE_AVX; it SIGSEGVs
        # generic builds (observed: winecx 21.2.0 crashes with it set).
        if is_cx and env.get("ROSETTA_ADVERTISE_AVX") is None:
            env["ROSETTA_ADVERTISE_AVX"] = "1"
        if env.get("ROSETTA_ADVERTISE_AVX") in ("0", ""):
            env.pop("ROSETTA_ADVERTISE_AVX", None)
        env["DOTNET_EnableWriteXorExecute"] = "0"

    # ---- bundled payloads -------------------------------------------------
    if eng.gptk_dir:
        shared = eng.gptk_dir / "external" / "libd3dshared.dylib"
        if shared.is_file():
            env["CX_APPLEGPTK_LIBD3DSHARED_PATH"] = str(shared)
    if eng.gstreamer_dir:
        env["GST_PLUGIN_SYSTEM_PATH"] = str(eng.gstreamer_dir)
        env["GST_REGISTRY"] = str(
            CROSSOVER_USER_DIR / "gstreamer-1.0-registry.x86_64.bin"
        )

    # ---- per-bottle [EnvironmentVariables] --------------------------------
    for key, value in bottle.env_vars().items():
        if key in ("CX_BOTTLE", "WINEPREFIX"):
            continue  # the perl wrapper refuses these too
        env[key] = expand_env(value, env)

    # ---- command-line overrides -------------------------------------------
    env.pop("CX_WINDOWS_VERSION", None)
    if opts.winver:
        env["CX_WINDOWS_VERSION"] = opts.winver
    env.pop("WINEDLLOVERRIDES", None)
    if opts.dlloverrides:
        env["WINEDLLOVERRIDES"] = opts.dlloverrides
    if opts.debugmsg is not None:
        env["WINEDEBUG"] = opts.debugmsg
    elif os.environ.get("CX_DEBUGMSG"):
        env["WINEDEBUG"] = os.environ["CX_DEBUGMSG"]
    env.pop("PYTHONPATH", None)  # don't leak host python into wine tools
    if opts.extra_env:
        for pair in shlex.split(opts.extra_env):
            if "=" in pair:
                k, v = pair.split("=", 1)
                env[k] = v
    return env


def _resolve_app(opts: LaunchOptions, argv: list[str]) -> list[str]:
    """Resolve the program argument like the perl wrapper does.

    Absolute Windows paths pass through; unix paths convert via Z:; bare
    names are searched for inside the bottle's drive_c.
    """
    if not argv:
        raise LaunchError("no application specified")
    app = argv[0]
    if opts.convert_paths:
        if is_win_path(app):
            pass
        elif Path(app).exists():
            app = unix_to_win_path(app)
        else:
            found = find_exe_in_drive_c(opts.bottle.path, app)
            if found is not None:
                app = unix_to_win_path(str(found))
            elif not re_search_win(app):
                # last resort: let wine resolve it (PATH/system32)
                pass
    args = []
    for arg in argv[1:]:
        if opts.convert_paths and Path(arg).exists() and not is_win_path(arg):
            args.append(unix_to_win_path(arg))
        else:
            args.append(arg)
    return [app, *args]


def re_search_win(value: str) -> bool:
    """True when the string looks like something wine itself can resolve
    (already a win path, or contains no path separators)."""
    return is_win_path(value) or "/" not in value


def build_command(opts: LaunchOptions, argv: list[str]) -> list[str]:
    """Compose the final exec vector."""
    eng = opts.engine
    if eng.kind == "cx":
        if not eng.wine_loader or not eng.winewrapper:
            raise LaunchError(f"cx engine at {eng.root} is missing wineloader/winewrapper")
        # Option flags go BEFORE the --run/--start marker: the perl wrapper
        # unshifts them ahead of "--run", "--" and only the target argv
        # follows the "--".
        cmd = [str(eng.wine_loader), str(eng.winewrapper)]
        if opts.new_console:
            cmd.append("--new-console")
        if opts.wait_children:
            cmd.append("--wait-children")
        if not opts.wait:
            cmd.append("--no-wait")
        if not opts.quote_args:
            cmd.append("--no-quotes")
        if not opts.convert_paths:
            cmd.append("--no-convert")
        if opts.workdir:
            cmd += ["--workdir", opts.workdir]
        if opts.desktop:
            cmd += ["--desktop", opts.desktop]
        if opts.start_document is not None:
            cmd += ["--start", "--"]
            doc = opts.start_document
            cmd.append(doc if is_win_path(doc) else unix_to_win_path(doc))
            return cmd
        cmd += ["--run", "--"]
        cmd += _resolve_app(opts, argv)
        return cmd

    # generic wine:  wine [explorer /desktop=...] program args…
    wine = eng.wine_bin or eng.wine_loader
    if wine is None:
        raise LaunchError(f"engine at {eng.root} has no wine executable")
    cmd = [str(wine)]
    if opts.start_document is not None:
        doc = opts.start_document
        cmd += ["start", "/unix" if not is_win_path(doc) else "", doc]
        return [c for c in cmd if c]
    if opts.desktop:
        geom = opts.desktop if "," in opts.desktop else f"{opts.desktop},1600x900"
        cmd += ["explorer", f"/desktop={geom}"]
    if opts.new_console:
        cmd += ["wineconsole", "--backend=user"]
    cmd += _resolve_app(opts, argv)
    return cmd


def run(opts: LaunchOptions, argv: list[str], env: dict[str, str] | None = None,
        capture: bool = False) -> int | subprocess.CompletedProcess:
    """Run a program. By default this *replaces* stdout/stderr passthrough and
    waits; ``capture`` returns CompletedProcess instead."""
    env = env or build_environment(opts)
    cmd = build_command(opts, argv)
    if opts.verbose:
        print("+ " + " ".join(cmd), file=sys.stderr)
    cwd = opts.workdir if (opts.workdir and not is_win_path(opts.workdir)) else None
    if capture:
        return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True)
    if not opts.wait:
        subprocess.Popen(cmd, env=env, cwd=cwd,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        return 0
    try:
        return subprocess.call(cmd, env=env, cwd=cwd)
    except KeyboardInterrupt:
        return 130


def run_unix(eng: Engine, bottle: Bottle | None, argv: list[str],
             extra_env: dict[str, str] | None = None,
             capture: bool = False) -> int | subprocess.CompletedProcess:
    """Run a host-side engine binary (wineserver, wineboot, regedit peers)
    with the bottle environment applied — the ``--ux-app`` equivalent."""
    opts = LaunchOptions(bottle=bottle or _scratch_bottle(), engine=eng)
    env = build_environment(opts)
    if extra_env:
        env.update(extra_env)
    if capture:
        return subprocess.run(list(argv), env=env, capture_output=True, text=True)
    return subprocess.call(list(argv), env=env)


def _scratch_bottle() -> Bottle:
    from .util import CxConfig
    return Bottle(Path("/nonexistent"), "scratch", CxConfig())


def wineserver_ctl(opts: LaunchOptions, flag: str) -> int:
    """``wineserver -k`` / ``-w`` / ``-p`` for a bottle."""
    env = build_environment(opts)
    ws = env.get("WINESERVER") or "wineserver"
    return subprocess.call([ws, flag], env=env)


def stop_bottle(opts: LaunchOptions) -> None:
    env = build_environment(opts)
    ws = env.get("WINESERVER") or "wineserver"
    subprocess.call([ws, "-k"], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_wine_app(opts: LaunchOptions, app: str, args: list[str],
                 arch: str = "auto", scope_private: bool = True,
                 capture: bool = False) -> int | subprocess.CompletedProcess:
    """Run a bundled wine tool (rundll32/regedit/wineboot…) by name — the
    ``--wl-app`` / ``--wl32-app`` equivalent. ``arch`` selects the PE dir."""
    eng = opts.engine
    exe: Path | None = None
    dirs = eng.dll_search_dirs(opts.bottle.winearch)
    if arch == "32":
        dirs = [d for d in dirs if d.name == "i386-windows"] or dirs
    elif arch == "64":
        dirs = [d for d in dirs if d.name != "i386-windows"] or dirs
    for d in dirs:
        cand = d / app
        if cand.is_file():
            exe = cand
            break
    if exe is None and eng.kind != "cx":
        # generic wine resolves builtin tools by name
        argv = [app, *args]
    elif exe is None:
        raise LaunchError(f"could not find {app} in WINEDLLPATH")
    else:
        argv = [unix_to_win_path(str(exe)), *args]
    opts2 = LaunchOptions(**{**opts.__dict__})
    opts2.convert_paths = False
    opts2.quote_args = False
    opts2.gui_dialogs = False
    return run(opts2, argv, capture=capture)
