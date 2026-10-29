"""decanter — command line interface."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import __version__
from .bottle import (
    Bottle, TEMPLATES, DEFAULT_TEMPLATE, archive_bottle, bottle_roots,
    copy_bottle, default_bottle_link, delete_bottle, find_bottle,
    get_default_bottle, list_bottles, rename_bottle, restore_bottle,
    set_default_bottle,
)
from .create import CreateError, create_bottle
from .engine import (
    Engine, default_engine, discover_engines, fetch_open_engine_releases,
    get_engine, install_engine, ENGINES_DIR,
)
from .programs import list_programs
from .recipes import install_app, list_recipes
from .launcher import (
    LaunchOptions, LaunchError, run as launch_run, run_unix, run_wine_app,
    stop_bottle, wineserver_ctl,
)
from .settings import KNOWN_KEYS, apply_config, GRAPHICS_BACKENDS
from .shortcuts import make_app
from .util import CxConfig, dir_size, human_size


def eprint(*a):
    print(*a, file=sys.stderr)


def _engine_or_fail(sel: str | None) -> Engine:
    eng = get_engine(sel)
    if eng is None:
        eprint("error: no usable wine engine found.")
        eprint("install CrossOver, or run `decanter engine install` to fetch an")
        eprint("open wine-crossover build, or point DECANTER_ENGINE at a wine")
        eprint("installation.")
        raise SystemExit(2)
    return eng


def _bottle_or_fail(name: str | None) -> Bottle:
    if name is None:
        name = os.environ.get("CX_BOTTLE") or "default"
    if name == "default":
        b = get_default_bottle()
        if b is None:
            b = find_bottle("default") or (
                list_bottles()[0] if list_bottles() else None)
        if b is None:
            eprint("error: no default bottle; create one with `decanter create`")
            raise SystemExit(2)
        return b
    b = find_bottle(name)
    if b is None or not b.valid:
        eprint(f"error: bottle {name!r} not found (searched: "
               + ", ".join(str(r) for r in bottle_roots()) + ")")
        raise SystemExit(2)
    return b


def _opts(args, bottle: Bottle, engine: Engine) -> LaunchOptions:
    return LaunchOptions(
        bottle=bottle, engine=engine,
        convert_paths=not getattr(args, "no_convert", False),
        quote_args=not getattr(args, "no_quotes", False),
        workdir=getattr(args, "workdir", None),
        desktop=getattr(args, "desktop", None),
        winver=getattr(args, "winver", None),
        dlloverrides=getattr(args, "dll", None),
        extra_env=getattr(args, "env", None),
        debugmsg=getattr(args, "debugmsg", None),
        new_console=getattr(args, "new_console", False),
        wait=not getattr(args, "no_wait", False),
        wait_children=getattr(args, "wait_children", False),
        verbose=getattr(args, "verbose", False),
    )


def _add_bottle_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("-b", "--bottle", help="bottle name (or absolute path); "
                   "falls back to $CX_BOTTLE then the default bottle")


def _add_engine_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("-e", "--engine", help="engine selector: name, kind, or path "
                   "(default: auto-detect, preferring CrossOver-style engines)")


def _add_launch_args(p: argparse.ArgumentParser) -> None:
    _add_bottle_arg(p)
    _add_engine_arg(p)
    p.add_argument("--desktop", help="run inside a virtual desktop NAME[,WxH]")
    p.add_argument("--workdir", help="working directory (unix path)")
    p.add_argument("--winver", help="override reported Windows version")
    p.add_argument("--dll", help="WINEDLLOVERRIDES string for this run")
    p.add_argument("--env", help="extra env vars: 'K=V K2=V2'")
    p.add_argument("--debugmsg", help="WINEDEBUG channels")
    p.add_argument("--no-convert", action="store_true",
                   help="don't convert unix paths to windows paths")
    p.add_argument("--no-quotes", action="store_true",
                   help="pass arguments without re-quoting")
    p.add_argument("--new-console", action="store_true",
                   help="open a console window for CLI apps")
    p.add_argument("--no-wait", action="store_true",
                   help="return immediately after starting the process")
    p.add_argument("--wait-children", action="store_true",
                   help="wait for the process and all its children")
    p.add_argument("-v", "--verbose", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="decanter",
        description="Open-source Windows-on-macOS game/app launcher "
                    "(CrossOver-compatible bottles)")
    parser.add_argument("--json", action="store_true",
                        help="machine-readable JSON output for list/status/"
                             "programs/engines/config/doctor")
    parser.add_argument("--version", action="version",
                        version=f"decanter {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    # -- doctor -------------------------------------------------------------
    sub.add_parser("doctor", help="check this machine: Rosetta, engines, bottles")

    # -- engines ------------------------------------------------------------
    p_eng = sub.add_parser("engines", aliases=["engine"],
                           help="list or install wine engines")
    eng_sub = p_eng.add_subparsers(dest="engine_cmd", metavar="action")
    eng_sub.add_parser("list", help="list discovered engines")
    p_inst = eng_sub.add_parser("install", help="install an open wine engine")
    p_inst.add_argument("--url", help="tarball URL (default: pick from "
                        "the open wine-crossover release feed)")
    p_inst.add_argument("--name", help="directory name under ~/.decanter/engines")
    p_inst.add_argument("--list", action="store_true",
                        help="list downloadable engines instead of installing")
    p_gptk = eng_sub.add_parser("setup-d3dmetal",
                              help="install Apple's D3DMetal.framework into an engine")
    p_gptk.add_argument("--dmg", required=True,
                        help="path to Apple's game-porting-toolkit DMG")
    p_gptk.add_argument("--engine", help="target engine (default: a decanter engine)")

    # -- bottle management ---------------------------------------------------
    sub.add_parser("list", aliases=["ls"], help="list bottles")
    p_prog = sub.add_parser("programs", aliases=["apps"],
                          help="list installed programs in a bottle")
    _add_bottle_arg(p_prog)
    p_status = sub.add_parser("status", aliases=["info"],
                              help="show bottle details")
    _add_bottle_arg(p_status)

    p_create = sub.add_parser("create", help="create a bottle")
    p_create.add_argument("name")
    p_create.add_argument("-t", "--template", default=DEFAULT_TEMPLATE,
                          choices=sorted(TEMPLATES),
                          help="windows version template (default: win10_64)")
    p_create.add_argument("--description")
    _add_engine_arg(p_create)

    p_del = sub.add_parser("delete", help="delete a bottle")
    p_del.add_argument("name")
    p_del.add_argument("-f", "--force", action="store_true")

    p_cp = sub.add_parser("copy", help="duplicate a bottle")
    p_cp.add_argument("source")
    p_cp.add_argument("name")

    p_rn = sub.add_parser("rename", help="rename a bottle")
    p_rn.add_argument("old")
    p_rn.add_argument("new")

    p_def = sub.add_parser("default", help="mark a bottle as the default")
    p_def.add_argument("name")

    p_ar = sub.add_parser("archive", help="export a bottle to .tar.gz")
    p_ar.add_argument("name")
    p_ar.add_argument("-o", "--output")

    p_rs = sub.add_parser("restore", help="import a bottle from .tar/.tar.gz")
    p_rs.add_argument("archive")
    p_rs.add_argument("--name")

    # -- running -------------------------------------------------------------
    p_run = sub.add_parser("run", help="run a Windows program in a bottle")
    _add_launch_args(p_run)
    p_run.add_argument("program", nargs=argparse.REMAINDER)

    p_start = sub.add_parser("start", help="open a document/exe through "
                             "Windows associations (like double-click)")
    _add_launch_args(p_start)
    p_start.add_argument("document")

    for tool in ("regedit", "winecfg", "control", "taskmgr", "explorer",
                 "wineboot", "uninstaller", "cmd", "notepad", "reg"):
        p_t = sub.add_parser(tool, help=f"run {tool} inside a bottle")
        _add_launch_args(p_t)
        p_t.add_argument("tool_args", nargs=argparse.REMAINDER)

    p_tricks = sub.add_parser("winetricks", help="run winetricks in a bottle")
    _add_bottle_arg(p_tricks)
    _add_engine_arg(p_tricks)
    p_tricks.add_argument("tricks_args", nargs=argparse.REMAINDER)

    p_kill = sub.add_parser("kill", help="kill all wine processes in a bottle")
    _add_bottle_arg(p_kill)
    _add_engine_arg(p_kill)
    p_wait = sub.add_parser("wait", help="wait for all wine processes in a bottle")
    _add_bottle_arg(p_wait)
    _add_engine_arg(p_wait)

    # -- configuration ---------------------------------------------------------
    p_cfg = sub.add_parser("config", help="per-bottle configuration")
    _add_bottle_arg(p_cfg)
    _add_engine_arg(p_cfg)
    cfg_sub = p_cfg.add_subparsers(dest="config_cmd", metavar="action")
    cfg_sub.add_parser("get", help="show effective bottle settings")
    cfg_sub.add_parser("keys", help="list known setting keys")
    p_set = cfg_sub.add_parser("set", help="set KEY=VALUE pairs")
    p_set.add_argument("pairs", nargs="+")

    # -- install recipes -----------------------------------------------------
    p_inst2 = sub.add_parser("install",
                             help="install a known application from a recipe")
    p_inst2.add_argument("recipe", nargs="?",
                         help="recipe id (see --list)")
    p_inst2.add_argument("-b", "--bottle", help="bottle name to create/use")
    p_inst2.add_argument("-t", "--template", help="override recipe bottle template")
    p_inst2.add_argument("-e", "--engine", help="engine selector")
    p_inst2.add_argument("--list", action="store_true",
                         help="list available recipes")
    p_inst2.add_argument("--dry-run", action="store_true",
                         help="show the install plan without executing")

    # -- shortcuts -----------------------------------------------------------
    p_sh = sub.add_parser("shortcut", help="create a double-clickable .app "
                          "for a Windows program")
    _add_bottle_arg(p_sh)
    p_sh.add_argument("--exe", required=True,
                      help="windows path or unix path of the .exe")
    p_sh.add_argument("--name", required=True)
    p_sh.add_argument("--icon", help="icns file")
    p_sh.add_argument("--dir", help="output dir (default ~/Applications)")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0

    try:
        return _dispatch(args)
    except (LaunchError, CreateError, ValueError, FileExistsError,
            FileNotFoundError) as exc:
        eprint(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


def _dispatch(args) -> int:
    cmd = args.command

    if cmd == "doctor":
        if args.json:
            return _json_doctor()
        return cmd_doctor()
    if cmd in ("engines", "engine"):
        if args.json and (args.engine_cmd or "list") == "list":
            default = default_engine()
            print(json.dumps([{"name": e.name, "kind": e.kind,
                               "version": e.version, "root": str(e.root),
                               "backends": e.available_backends(),
                               "templates": e.templates(),
                               "default": e == default}
                              for e in discover_engines()]))
            return 0
        return cmd_engines(args)
    if cmd in ("list", "ls"):
        if args.json:
            return _json_list()
        return cmd_list(args)
    if cmd in ("programs", "apps"):
        b = _bottle_or_fail(args.bottle)
        progs = list_programs(b)
        if args.json:
            print(json.dumps(progs))
        else:
            for p in progs:
                print(f"{p['name']}\n    {p.get('target') or '(no target)'}")
        return 0
    if cmd in ("status", "info"):
        b = _bottle_or_fail(args.bottle)
        d = b.describe()
        d["size_bytes"] = dir_size(b.path)
        if args.json:
            print(json.dumps(d))
        else:
            print(json.dumps(d, indent=2))
            print(f"size: {human_size(d['size_bytes'])}")
        return 0
    if cmd == "create":
        eng = _engine_or_fail(args.engine)
        b = create_bottle(args.name, eng, template=args.template,
                          description=args.description)
        print(f"created bottle {b.name!r} at {b.path}")
        return 0
    if cmd == "delete":
        b = _bottle_or_fail(args.name)
        if not args.force:
            eprint(f"about to permanently delete {b.path}")
            eprint("re-run with --force to confirm")
            return 2
        delete_bottle(b)
        print(f"deleted {b.name!r}")
        return 0
    if cmd == "copy":
        src = _bottle_or_fail(args.source)
        b = copy_bottle(src, args.name)
        print(f"copied to {b.path}")
        return 0
    if cmd == "rename":
        b = _bottle_or_fail(args.old)
        nb = rename_bottle(b, args.new)
        print(f"renamed to {nb.name!r}")
        return 0
    if cmd == "default":
        b = _bottle_or_fail(args.name)
        set_default_bottle(b)
        print(f"default bottle is now {b.name!r}")
        return 0
    if cmd == "archive":
        b = _bottle_or_fail(args.name)
        out = Path(args.output) if args.output else Path.cwd() / f"{b.name}.tar.gz"
        archive_bottle(b, out)
        print(f"archived {b.name!r} -> {out}")
        return 0
    if cmd == "restore":
        b = restore_bottle(Path(args.archive),
                           name=args.name)
        print(f"restored {b.name!r} at {b.path}")
        return 0
    if cmd == "run":
        return cmd_run(args)
    if cmd == "start":
        return cmd_start(args)
    if cmd in ("regedit", "winecfg", "control", "taskmgr", "explorer",
               "wineboot", "uninstaller", "cmd", "notepad", "reg"):
        return cmd_tool(args)
    if cmd == "winetricks":
        return cmd_winetricks(args)
    if cmd == "kill":
        b = _bottle_or_fail(args.bottle)
        eng = _engine_or_fail(args.engine)
        wineserver_ctl(LaunchOptions(bottle=b, engine=eng), "-k")
        return 0
    if cmd == "wait":
        b = _bottle_or_fail(args.bottle)
        eng = _engine_or_fail(args.engine)
        wineserver_ctl(LaunchOptions(bottle=b, engine=eng), "-w")
        return 0
    if cmd == "config":
        if args.json and (args.config_cmd or "get") == "get":
            b = _bottle_or_fail(args.bottle)
            print(json.dumps({"name": b.name, "path": str(b.path),
                              "template": b.template,
                              "arch": b.winearch,
                              "windows_version": b.windows_version,
                              "env": b.env_vars()}))
            return 0
        return cmd_config(args)
    if cmd == "install":
        if args.list or not args.recipe:
            recs = list_recipes()
            if args.json:
                print(json.dumps(recs))
                return 0
            if not recs:
                print("no recipes found")
                return 1
            for r in recs:
                print(f"{r['id']:<24} {r['name']} — {r.get('description', '')[:60]}")
            if not args.recipe:
                print("\nusage: decanter install RECIPE [-b BOTTLE_NAME]")
            return 0 if recs else 1
        eng = _engine_or_fail(args.engine)
        b = install_app(args.recipe, args.bottle, eng,
                        template=args.template, dry_run=args.dry_run)
        if b:
            print(f"installed {args.recipe} into {b.name!r}")
        return 0
    if cmd == "shortcut":
        b = _bottle_or_fail(args.bottle)
        target = args.exe
        if Path(target).exists():
            from .util import unix_to_win_path
            target = unix_to_win_path(target)
        # prefer an absolute path to this decanter so the app works
        # even when decanter isn't on PATH
        shim = Path(__file__).resolve().parent.parent / "bin" / "decanter"
        decanter_bin = str(shim) if shim.is_file() else "decanter"
        app = make_app(args.name, b.name, target,
                       dest_dir=Path(args.dir).expanduser() if args.dir else None,
                       decanter_bin=decanter_bin,
                       icon=Path(args.icon).expanduser() if args.icon else None)
        print(f"created {app}")
        return 0
    return 0


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------

def _json_list() -> int:
    default = get_default_bottle()
    out = []
    for b in list_bottles():
        d = b.describe()
        d["default"] = bool(default and default.path == b.path)
        out.append(d)
    print(json.dumps(out))
    return 0


def _json_doctor() -> int:
    rosetta = subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"],
                             capture_output=True).returncode == 0
    print(json.dumps({
        "decanter_version": __version__,
        "machine": platform.machine(),
        "macos": platform.mac_ver()[0],
        "rosetta": rosetta,
        "engines": [{"name": e.name, "kind": e.kind, "version": e.version,
                     "backends": e.available_backends(),
                     "root": str(e.root)}
                    for e in discover_engines()],
        "bottle_count": len(list_bottles()),
    }))
    return 0


def cmd_doctor() -> int:
    print(f"decanter {__version__}")
    print(f"host: {platform.machine()} / macOS {platform.mac_ver()[0]}")
    # Rosetta
    rosetta = subprocess.run(["/usr/bin/arch", "-x86_64", "/usr/bin/true"],
                             capture_output=True)
    print(f"rosetta 2: {'installed' if rosetta.returncode == 0 else 'MISSING (needed for x86_64 wine)'}")
    engines = discover_engines()
    if not engines:
        print("engines: none found")
        print("  -> install CrossOver, or `decanter engine install` for an open build")
    for e in engines:
        print(f"engine: {e.name}")
        print(f"  root: {e.root}")
        print(f"  kind: {e.kind}  version: {e.version or '?'}")
        print(f"  graphics backends: {', '.join(e.available_backends())}")
        if e.templates():
            print(f"  bottle templates: {', '.join(e.templates())}")
    bottles = list_bottles()
    print(f"bottles: {len(bottles)}")
    for b in bottles:
        env = b.env_vars()
        print(f"  {b.name} [{b.template or 'custom'}/{b.winearch}] "
              f"backend={env.get('CX_GRAPHICS_BACKEND', 'auto')} {b.path}")
    return 0


def cmd_engines(args) -> int:
    action = args.engine_cmd or "list"
    if action == "list":
        engines = discover_engines()
        if not engines:
            print("no engines discovered")
            return 1
        for e in engines:
            mark = " (default)" if e == default_engine() else ""
            print(f"{e.name}{mark}\n  kind={e.kind} version={e.version or '?'}\n"
                  f"  backends={','.join(e.available_backends())}\n  root={e.root}")
        return 0
    if action == "install":
        if args.list or not args.url:
            try:
                releases = fetch_open_engine_releases()
            except Exception as exc:
                eprint(f"could not fetch release feed: {exc}")
                if not args.url:
                    return 1
                releases = []
            for rel in releases:
                print(f"release {rel['tag']}")
                for a in rel["assets"]:
                    print(f"  {a['name']}  ({human_size(a['size'])})\n    {a['url']}")
            if not args.url:
                print("\npick one: decanter engine install --url URL [--name NAME]")
                return 0
        dest = install_engine(args.url, args.name)
        print(f"installed engine at {dest}")
        eng = get_engine(str(dest))
        if eng:
            print(f"kind={eng.kind} backends={','.join(eng.available_backends())}")
        else:
            eprint("warning: extracted files don't look like a wine engine; "
                   "check --name or the tarball layout")
        return 0
    if action == "setup-d3dmetal":
        return cmd_setup_d3dmetal(args)
    return 0


def cmd_setup_d3dmetal(args) -> int:
    """Copy Apple GPTK payloads (D3DMetal.framework, libd3dshared, PE dlls)
    into a decanter engine's lib64 — the open-source-legal way to use D3DMetal
    with a non-CrossOver wine build (user supplies Apple's dmg)."""
    dmg = Path(args.dmg).expanduser()
    if not dmg.is_file():
        eprint(f"error: {dmg} not found — download the Game Porting Toolkit "
               "dmg from developer.apple.com (free Apple account)")
        return 2
    eng = get_engine(args.engine)
    if eng is None:
        eprint("error: no target engine; install one first "
               "(`decanter engine install`)")
        return 2
    with tempfile.TemporaryDirectory() as td:
        mount = Path(td) / "mnt"
        mount.mkdir()
        rc = subprocess.call(["hdiutil", "attach", str(dmg),
                              "-mountpoint", str(mount), "-nobrowse", "-quiet"])
        if rc:
            eprint("error: could not mount the dmg")
            return 1
        try:
            # GPTK dmg contains a pkg; payload lives under lib/ + windows dlls
            found = []
            for cand in mount.rglob("*"):
                if cand.name in ("D3DMetal.framework", "libd3dshared.dylib",
                                 "apple_gptk"):
                    found.append(cand)
            pkg = next(mount.rglob("*.pkg"), None)
            payload_root = None
            if pkg:
                expand = Path(td) / "pkg"
                subprocess.call(["pkgutil", "--expand-full", str(pkg),
                                 str(expand)], capture_output=True)
                for cand in expand.rglob("apple_gptk"):
                    payload_root = cand
                    break
                if payload_root is None:
                    for cand in expand.rglob("D3DMetal.framework"):
                        payload_root = cand.parent.parent
                        break
            if payload_root is None:
                for cand in found:
                    if cand.name == "apple_gptk":
                        payload_root = cand
            if payload_root is None:
                eprint("error: could not locate apple_gptk payload in the dmg")
                return 1
            lib64 = eng.root / "lib64"
            lib64.mkdir(exist_ok=True)
            dest = lib64 / "apple_gptk"
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(payload_root, dest, symlinks=True)
            print(f"installed D3DMetal payload -> {dest}")
            print("set per bottle: decanter config -b BOTTLE set backend=d3dmetal")
        finally:
            subprocess.call(["hdiutil", "detach", str(mount), "-quiet"])
    return 0


def cmd_list(args) -> int:
    bottles = list_bottles()
    if not bottles:
        print("no bottles found — create one with `decanter create NAME`")
        return 0
    default = get_default_bottle()
    for b in bottles:
        env = b.env_vars()
        tags = []
        if default and default.path == b.path:
            tags.append("default")
        backend = env.get("CX_GRAPHICS_BACKEND")
        if backend:
            tags.append(f"backend={backend}")
        sync = [k for k in ("WINEMSYNC", "WINEESYNC", "WINEFSYNC")
                if env.get(k) == "1"]
        if sync:
            tags.append("+".join(s[4:].lower() for s in sync))
        tag = f"  [{', '.join(tags)}]" if tags else ""
        print(f"{b.name}  ({b.template or 'custom'}, {b.winearch}){tag}\n"
              f"    {b.path}")
    return 0


def cmd_run(args) -> int:
    argv = list(args.program)
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        eprint("error: no program specified")
        return 2
    bottle = _bottle_or_fail(args.bottle)
    eng = _engine_or_fail(args.engine)
    opts = _opts(args, bottle, eng)
    rc = launch_run(opts, argv)
    return rc if isinstance(rc, int) else rc.returncode


def cmd_start(args) -> int:
    bottle = _bottle_or_fail(args.bottle)
    eng = _engine_or_fail(args.engine)
    opts = _opts(args, bottle, eng)
    opts.start_document = args.document
    rc = launch_run(opts, [])
    return rc if isinstance(rc, int) else rc.returncode


def cmd_tool(args) -> int:
    bottle = _bottle_or_fail(args.bottle)
    eng = _engine_or_fail(args.engine)
    tool = args.command
    exe_map = {
        "regedit": "regedit.exe", "reg": "reg.exe",
        "winecfg": "winecfg.exe", "control": "control.exe",
        "taskmgr": "taskmgr.exe", "explorer": "explorer.exe",
        "wineboot": "wineboot.exe", "uninstaller": "uninstaller.exe",
        "cmd": "cmd.exe", "notepad": "notepad.exe",
    }
    argv = list(args.tool_args)
    if argv and argv[0] == "--":
        argv = argv[1:]
    opts = _opts(args, bottle, eng)
    exe = exe_map[tool]
    if eng.kind == "cx":
        rc = run_wine_app(opts, exe, argv)
        return rc if isinstance(rc, int) else rc.returncode
    rc = launch_run(opts, [exe, *argv])
    return rc if isinstance(rc, int) else rc.returncode


def cmd_winetricks(args) -> int:
    bottle = _bottle_or_fail(args.bottle)
    eng = _engine_or_fail(args.engine)
    wt = shutil.which("winetricks")
    if wt is None:
        eprint("winetricks not found — install it (`brew install winetricks`)")
        return 2
    env = {}
    rc = run_unix(eng, bottle, [wt, *args.tricks_args], extra_env=env)
    return rc if isinstance(rc, int) else rc.returncode


def cmd_config(args) -> int:
    action = args.config_cmd or "get"
    if action == "keys":
        for line in KNOWN_KEYS:
            print(line)
        return 0
    bottle = _bottle_or_fail(args.bottle)
    if action == "get":
        cfg = CxConfig(bottle.conf_path)
        print(f"[{bottle.name}] {bottle.path}")
        print(f"template={bottle.template} arch={bottle.winearch}")
        env = bottle.env_vars()
        if env:
            print("[EnvironmentVariables]")
            for k, v in env.items():
                print(f"  {k}={v}")
        else:
            print("(no per-bottle env vars)")
        return 0
    if action == "set":
        eng = _engine_or_fail(args.engine)
        for pair in args.pairs:
            if "=" not in pair:
                eprint(f"skipping {pair!r}: expected KEY=VALUE")
                continue
            key, value = pair.split("=", 1)
            apply_config(bottle, eng, key, value)
            print(f"{key}={value}")
        return 0
    return 0
