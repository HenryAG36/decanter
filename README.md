# Decanter

An open-source way to run Windows games and apps on macOS — a
CrossOver-compatible bottle manager and launcher built by reverse-engineering
how CrossOver actually works.

Decanter reproduces the exact environment, bottle format, and launch contract
that CrossOver's perl `bin/wine` wrapper builds, so anything that runs under
CrossOver runs identically under Decanter — including the same bottles.

```
decanter create Steam -t win10_64
decanter config -b Steam set backend=d3dmetal msync=1
decanter run -b Steam -- "C:\Program Files (x86)\Steam\steam.exe"
```

## Why this doesn't lose compatibility

CrossOver is mostly glue: the real work is done by its patched Wine binaries
and bundled translation layers (D3DMetal, DXMT, DXVK, MoltenVK, gstreamer).
Those binaries decide everything through a documented-on-disk contract:

* `WINEPREFIX`, `WINEDLLPATH`, `WINELOADER`, `WINESERVER`, `WINEWRAPPER`
* `CX_GRAPHICS_BACKEND=d3dmetal|dxmt|dxvk|wined3d` — the per-bottle graphics
  toggle CrossOver's GUI writes into `cxbottle.conf` `[EnvironmentVariables]`
* `WINEMSYNC=1` — Mach-port sync primitives (their macOS esync/fsync answer)
* `ROSETTA_ADVERTISE_AVX=1` — exposes AVX to games under Rosetta 2
* `CX_APPLEGPTK_LIBD3DSHARED_PATH`, `GST_*`, `DOTNET_EnableWriteXorExecute`

Decanter builds that same contract (see `docs/REVERSE-ENGINEERING.md` for the
full findings) and execs the *same* binaries — so DX11→Metal, msync, bottle
templates, and winetricks-level tweaks all behave the same way.

## Engines

Decanter is engine-agnostic. `decanter engines` shows what was found.

| engine | kind | notes |
|---|---|---|
| **CrossOver.app** (installed) | `cx` | full feature set: D3DMetal, DXMT, DXVK, msync, all templates |
| **open wine-crossover builds** | `generic`/`cx` | built from CodeWeavers' LGPL-published source (e.g. Heroic's `wine-crossover`, Kegworks/Sikarugir `WineCX`) — the fully-open path |
| **wine-devel/staging** (Gcenx `macOS_Wine_builds`) | `generic` | vanilla Wine, esync/fsync |
| **Apple GPTK** | `generic` | D3DMetal built in |

Install an open engine without CrossOver:

```
decanter engine install --list
decanter engine install --url <tarball-url>
decanter engine setup-d3dmetal --dmg /path/to/game-porting-toolkit.dmg   # adds Apple's D3DMetal to a decanter engine
```

## Bottles

Bottles are ordinary Wine prefixes carrying a `cxbottle.conf`, fully
interoperable with CrossOver:

* `decanter list` shows bottles from `~/Library/Application Support/CrossOver/Bottles`,
  `~/.decanter/bottles`, and `$DECANTER_BOTTLE_PATH`/`$CX_BOTTLE_PATH`
* Bottles Decanter creates in the CrossOver dir **show up in CrossOver's own
  GUI** and pass `cxbottle --status` as `uptodate`
* Decanter drives bottles CrossOver created (e.g. your existing Steam bottle)

```
decanter create MyGames -t win11_64
decanter copy MyGames MyGames-backup
decanter archive MyGames -o MyGames.tar.gz
decanter restore MyGames.tar.gz
decanter delete MyGames --force
decanter default MyGames
```

Templates: `win98 win2000 winxp(_64) winvista(_64) win7(_64) win8(_64)
win10(_64) win11_64` — the same set CrossOver ships, initialised through the
same `crossover.inf` `InstallHinfSection` path when a cx engine is present,
or plain `wineboot` on generic engines.

## Running

```
decanter run -b BOTTLE -- program.exe [args]      # run a program
decanter run -b BOTTLE -- game                    # bare name: searched in drive_c
decanter run -b BOTTLE -- /unix/path/setup.exe    # unix paths convert via Z:
decanter run -b BOTTLE --desktop g,1600x900 -- game.exe
decanter start -b BOTTLE ./save.sav               # open via Windows associations
decanter regedit -b BOTTLE                        # bundled tools:
decanter winecfg|control|taskmgr|explorer|wineboot|cmd -b BOTTLE
decanter winetricks -b BOTTLE vcrun2022 dxvk
decanter kill -b BOTTLE                           # wineserver -k
decanter wait -b BOTTLE                           # wait for all wine processes
```

Useful flags: `--winver`, `--dll OVERRIDES`, `--env "K=V ..."`, `--debugmsg`,
`--new-console`, `--no-wait`, `--wait-children`, `--no-convert`, `-v`.

## Per-bottle configuration

```
decanter config -b BOTTLE get
decanter config -b BOTTLE keys
decanter config -b BOTTLE set backend=d3dmetal msync=1 winver=win11 retina=1
decanter config -b BOTTLE set override:d3d11=native,builtin
decanter config -b BOTTLE set env:DXVK_HUD=devinfo,fps
```

| key | meaning |
|---|---|
| `backend` | `d3dmetal`, `dxmt`, `dxvk`, `wined3d`, `auto` — Direct3D translation stack |
| `msync`/`esync`/`fsync` | sync primitives |
| `winver` | reported Windows version (written to the registry) |
| `retina` | HiDPI rendering (Mac Driver `RetinaMode`) |
| `override:<dll>` | DLL override (`native`, `builtin`, `native,builtin`, `disabled`) |
| `env:<VAR>` | any per-bottle environment variable |

Settings land exactly where CrossOver puts them: `[EnvironmentVariables]` in
`cxbottle.conf` and `HKCU\Software\Wine\...` in the registry — so bottles stay
bidirectionally compatible.

## Desktop integration

```
decanter shortcut -b BOTTLE --exe "C:\GOG\game.exe" --name "My Game"
```

creates `~/Applications/My Game.app` that double-click-launches the game.

## GUI

`Decanter.app` — a native SwiftUI frontend mirroring CrossOver's workflow
with original code/design:

```
cd ui && ./build.sh && open ../build/Decanter.app
```

* Sidebar of bottles + Home view aggregating every installed program
* Per-bottle program grid (double-click to launch, right-click for
  .app shortcuts) — reads the same `desktopdata/cxmenu` + Start Menu
  metadata CrossOver's UI uses
* Bottle Settings: graphics backend (Auto/D3DMetal/DXMT/DXVK/wined3d),
  Windows version, MSync, High-Resolution, NVAPI/DLSS, esync/fsync —
  with CrossOver-style reboot warnings
* Install Software, Run Command (with captured output panel), New
  Bottle, Restore Archive, Rename/Duplicate/Delete/Force Quit,
  Wine tools (winecfg, regedit, control, taskmgr, cmd, explorer,
  uninstaller) and a live command log
* Preferences: engine inventory, diagnostics, doctor info

The app bundles the python backend in `Contents/Resources/python` and
runs it via `/usr/bin/python3 -m decanter`, or uses `DECANTER_BIN`/a
`decanter` on PATH. Every UI action maps 1:1 to the CLI, which stays
fully usable on its own. `--json` output (`list`, `status`, `programs`,
`engines list`, `config get`, `doctor`) is the IPC contract.

## Requirements

* macOS on Apple Silicon or Intel
* Rosetta 2 on Apple Silicon (x86_64 wine builds run through it)
* Python 3.9+ (stdlib only — no dependencies)
* At least one engine (CrossOver.app, or an installed open engine)

## Install

```
git clone <repo> && cd decanter
sudo ln -s "$PWD/bin/decanter" /usr/local/bin/decanter   # or add bin/ to PATH
```

## Legal / provenance

* Decanter itself is MIT-licensed original code.
* CrossOver's Wine source is LGPL and published by CodeWeavers; open builds
  of it (Heroic `wine-crossover`, `WineCX`, homebrew `wine-crossover`) are
  the fully-open engine path.
* `D3DMetal.framework` is Apple's proprietary component from the free Game
  Porting Toolkit — install it yourself from Apple's dmg
  (`decanter engine setup-d3dmetal --dmg ...`). It's never downloaded or
  redistributed by Decanter.
* Using an installed CrossOver.app as an engine uses binaries the user
  already has locally — Decanter ships nothing from CrossOver.

See `docs/REVERSE-ENGINEERING.md` for the technical write-up of how the
contract was derived.
