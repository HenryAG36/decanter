# Reverse-engineering CrossOver 26.3 on macOS

How Decanter's launch contract was derived. Target:
`/Applications/CrossOver.app/Contents/SharedSupport/CrossOver` (= `CX_ROOT`),
CrossOver 26.3.0.39832 on Apple Silicon / macOS, analysed with the
[morluto/rea](https://github.com/morluto/rea) methodology — mostly
source-level, because CrossOver's entire glue layer is shipped as readable
Perl.

## 1. What's inside CX_ROOT

```
bin/    wine cxstart cxbottle cxtemplate cxreboot cxmenu ...   (perl scripts)
        wineloader wineserver cabextract unrar                  (x86_64 Mach-O)
lib/    perl/CX*.pm            (CXBottle, CXConfig, CXUtils, CXRWConfig, ...)
        wine/{i386,x86_64}-windows/   builtin PE dlls + winewrapper.exe
        wine/x86_64-unix/             unix-side wine libs (ntdll.so, ...)
        dxvk/{i386,x86_64}-windows/   DXVK d3d9-11 dlls
        dxmt/{i386,x86_64}-windows/ + x86_64-unix/winemetal.so
        cxupgrade/
lib64/  apple_gptk/wine/x86_64-windows/   (d3d11/12, dxgi, nvapi64, ...)
        apple_gptk/external/D3DMetal.framework + libd3dshared.dylib
        libMoltenVK.dylib libSDL2 gstreamer-1.0/ libfreetype ...
share/crossover/
        bottle_templates/{win98,win2000,winxp*,winvista*,win7*,win8*,win10*,win11_64}
        bottle_data/{crossover.inf,cxbottle.conf,oem0.cat}
        data/{CrossOver.conf,crossover.tie,cxfixes.xml}
etc/    CrossOver.conf
```

`wineloader`/`wineserver` are x86_64 binaries → everything runs through
Rosetta 2 on Apple Silicon.

## 2. The launch path (`bin/wine`, a perl script)

1. `CX_ROOT` located by walking up from argv[0] to the dir containing
   `bin/cxmenu`.
2. `--bottle NAME` or `$CX_BOTTLE` (default `default`) →
   `CXBottle::setup_bottle_wineprefix`:
   * private bottles: each dir in `$CX_BOTTLE_PATH` →
     `~/Library/Application Support/CrossOver/Bottles/NAME`
   * managed bottles: `$CX_MANAGED_BOTTLE_PATH` → `/Library/.../Bottles`
   * valid iff `<dir>/system.reg` exists; `WINEPREFIX` = realpath.
3. Reads `<prefix>/cxbottle.conf` (custom INI: `"Key" = "value"`, `;;`
   comments). `[EnvironmentVariables]` entries are exported into the
   process env — **this is where CrossOver stores per-bottle toggles**:
   observed `CX_GRAPHICS_BACKEND=d3dmetal` and `WINEMSYNC=1` on the Steam
   bottle.
4. Builds the runtime env (from `setup_wine_environment`):
   * `PATH` += `$CX_ROOT/bin`
   * `WINEDLLPATH` = `lib/wine/x86_64-windows : lib/wine/i386-windows :
     lib/wine` for `win64` bottles (`aarch64-windows` host when present);
     `i386-windows`-only for `win32`
   * `WINELOADER=$CX_ROOT/bin/wineloader`,
     `WINESERVER=$CX_ROOT/bin/wineserver`,
     `WINEWRAPPER=<first winewrapper.exe in WINEDLLPATH>`
   * `CX_HOME=~/Library/Application Support/CrossOver`,
     `CX_INITIALIZED=<uid>:<bottle>`
   * `CX_APPLEGPTK_LIBD3DSHARED_PATH=$CX_ROOT/lib64/apple_gptk/external/
     libd3dshared.dylib` whenever that file exists
   * `GST_PLUGIN_SYSTEM_PATH`, `GST_REGISTRY` when lib64/gstreamer-1.0 exists
   * `DOTNET_EnableWriteXorExecute=0` (.NET under Rosetta fix)
   * `ROSETTA_ADVERTISE_AVX=1` unless the user set `0`/empty (Rosetta treats
     any value as on → must unset to disable)
   * `CX_WINDOWS_VERSION` only for bottle ops / `--winver`
   * `WINEDLLOVERRIDES`, `WINEDEBUG` from flags
5. `exec $WINELOADER $WINEWRAPPER [flags] --run -- <app> <args>`

   Flags are unshifted *before* `--run --`: `--desktop X --workdir X
   --no-convert --no-quotes --no-wait --wait-children --new-console`.
   For document-open: `--start -- <doc>`.

   Program resolution for bare names: glob-search `drive_c` (depth ≤3,
   preferring `windows/system32`); existing unix paths convert to `Z:`.

## 3. Bottle creation (`CXBT_<template>->create`)

`cxbottle --bottle X --create --template win10_64` runs
`share/crossover/bottle_templates/win10_64/setup --create`:

1. Write `cxbottle.conf` from `bottle_data/cxbottle.conf` + `[Bottle]` fields:
   `BottleID` (uuid), `Timestamp` = engine `BuildTimestamp` (NOT creation
   time — this is what `cxbottle --status` compares to report `uptodate`),
   `Encoding`, `Template`, `WineArch`, Menu* keys.
2. Write `system.reg` stub: `WINE REGISTRY Version 2\n#arch=win64` (before
   wineserver exists — avoids a creation race).
3. `run_inf`: `wine --wl-app rundll32.exe --no-quotes --desktop root
   --dll advpack=b;atl=b;oleaut32=b;rpcrt4=b;shdocvw=b;*iexplore.exe=b
   setupapi.dll,InstallHinfSection <Winver>Install 128 crossover.inf`
   (e.g. `Win10Install`). On win64 with i386-windows present, repeated via
   `--wl32-app` for the 32-bit side. First wineserver start also applies
   `wine.inf` automatically.
4. `dosdevices/y:` → `$HOME` symlink.
5. `regedit` tweaks (Outlook security) → `wineserver -w`.
6. `run_bottle_hooks(["create", templatedir])` — executes any `scripts.d`
   dirs (none shipped inside the app).

## 4. Graphics backends

Payloads shipped: `lib64/apple_gptk` (D3DMetal.framework + wrapper PE dlls),
`lib/dxmt`, `lib/dxvk` (+`lib64/libMoltenVK.dylib`).

The bottle's `system32/d3d11.dll` is the stock wine builtin (md5-verified) —
backends are **not** swapped by copying dlls. `CX_GRAPHICS_BACKEND` is read
by the wine binaries themselves (string present in `bin/wineserver`) which
route D3D calls internally, locating backend payloads relative to `CX_ROOT`
(`CX_APPLEGPTK_LIBD3DSHARED_PATH` points the unix side at the bridge).

So on a cx engine, selecting a backend = writing
`CX_GRAPHICS_BACKEND=d3dmetal|dxmt|dxvk` into `[EnvironmentVariables]`.
On generic engines there is no dispatcher → decanter does classic DXVK-style
deployment (copy dlls to system32/syswow64 + `native` DllOverrides).

## 5. Sync primitives

`WINEMSYNC=1` → msync, CrossOver's Mach-port-based replacement for
esync/fsync (confirmed live: `msync: bootstrapped mach port ... msync: up
and running.`). Generic wines use `WINEESYNC`/`WINEFSYNC` instead.

## 6. Other surface

* `cxbottle --status` → `Mode=private` + `Status=uptodate|upgrade`
  (Timestamp vs BuildTimestamp check — decanter writes the engine's
  BuildTimestamp so bottles report `uptodate`)
* `cxbottle` full surface: --create/--delete/--copy/--default/--tar/
  --restore/--install/--uninstall/--status — mirrored by decanter's
  create/delete/copy/default/archive/restore/status.
* Managed/stub bottles: `[ManagedUpdatePolicy]` regex rules (Link/Copy/
  Registry/Delete/Ignore + ReplaceFiles/ReplaceDirs/ReplaceConflicting)
  drive `wineprefixcreate --ref-dir` replication. Decanter treats managed
  roots as a read/lookup path.
* `crossover.tie`/`cxfixes.xml` = the app-compat database (install
  recipes); `lib/wine/x86_64-unix/cxcompatdb.so` = compiled-in fixes.

## 7. Open-source engine provenance

* CodeWeavers publishes wine-crossover source (LGPL) at
  `media.codeweavers.com/pub/crossover/source/` — open builds exist as
  `Heroic-Games-Launcher/wine-crossover` (verified: `wine-8.0.1
  (CrossOverFOSS 23.7.1)`), Sikarugir `WineCX` engines, Gcenx
  `macOS_Wine_builds` (vanilla), WhiskyWine.
* `D3DMetal.framework` is proprietary Apple — user installs from the free
  GPTK dmg; decanter only copies it into place (`engine setup-d3dmetal`).

## 8. GUI surface (CrossOver 26.3)

Observed from the app bundle (`Contents/Resources/Base.lproj` nibs +
`Localizable.strings`); decanter's SwiftUI UI implements the same
workflows with original code/layout.

* `MainWindow` + `IconCollectionView`/`LauncherIconView`: sidebar of
  bottles ("HOME", "PRIVATE BOTTLES") + icon grid of installed programs.
* Per-bottle actions: Install Software, Run Command (with captured
  "Command results"), Open C: drive, Rename, Duplicate, Export to
  Archive, Delete, Force Quit, Clear/Rebuild Programs, Make Default.
* Per-bottle toggles: graphics backend (Auto/D3DMetal/DXMT/DXVK/Wine),
  Vulkan backend (MoltenVK), DLSS for D3DMetal/DXMT, MSync,
  High-Resolution mode, CrossOver Preview flag — all stored as
  `[EnvironmentVariables]` in cxbottle.conf, toggles warn about reboot.
* Install flow: CrossTie "installation recipes" with bottle-type
  compatibility ratings; bottle templates grouped compatible/
  incompatible; dependency chain + download progress.
* Program discovery: `desktopdata/cxmenu/<encoded-dir>/<name>.lnk|.url`
  files are `/bin/sh` wrappers (`exec wine --bottle X --start
  "C:/path/real.lnk"`) — decanter resolves `--start` → the in-bottle
  .lnk/.url, then parses the target (Shell Link local path or INI URL).
  Filenames escape as `+`=space, `^XX`=hex.
* Paid/licensing surfaces (DemoNag, SerialCodeRegister, Publish,
  SubmitRating, CrossTie recipe DB) deliberately not reproduced.
