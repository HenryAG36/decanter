"""Wine engine discovery and management.

An *engine* is a Wine installation directory. Decanter supports several
layouts:

``cx``
    A CrossOver-style runtime (``CX_ROOT``): ``bin/wine`` is the perl
    wrapper, real binaries are ``bin/wineloader`` + ``bin/wineserver``,
    PE DLLs live under ``lib/wine/{i386,x86_64,aarch64}-windows`` and
    extra payloads (apple_gptk, dxvk, dxmt, MoltenVK, gstreamer) under
    ``lib64`` / ``lib``. This layout is produced both by an installed
    CrossOver.app and by open ``wine-crossover`` builds (built from
    CodeWeavers' LGPL-published source).

``generic``
    A vanilla Wine layout (WineHQ, wine-stable, Kegworks, Wineskin,
    Apple Game Porting Toolkit): ``bin/wine`` is a real launcher binary
    or shell script and accepts the usual ``wine program.exe`` form.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

DECANTER_HOME = Path(os.environ.get("DECANTER_HOME", Path.home() / ".decanter"))
ENGINES_DIR = DECANTER_HOME / "engines"

# Public release feeds for open wine builds:
#  * Gcenx/macOS_Wine_builds — vanilla wine stable/devel/staging (osx64)
#  * Heroic-Games-Launcher/wine-crossover — open wine-crossover builds
#    (CodeWeavers' LGPL-published source; closest to CrossOver itself)
#  * Sikarugir-App/Engines — WineCX + WhiskyWine (GPTK-derived) builds
OPEN_ENGINE_FEEDS = [
    "https://api.github.com/repos/Heroic-Games-Launcher/wine-crossover/releases?per_page=5",
    "https://api.github.com/repos/Gcenx/macOS_Wine_builds/releases?per_page=6",
    "https://api.github.com/repos/Sikarugir-App/Engines/releases?per_page=5",
]

CROSSOVER_APPS = [
    Path("/Applications/CrossOver.app"),
    Path.home() / "Applications/CrossOver.app",
]


@dataclass
class Engine:
    """A discovered wine runtime."""

    root: Path
    kind: str  # "cx" | "generic"
    name: str = ""
    version: str = ""
    bin_dir: Path = field(init=False)
    lib_dirs: list[Path] = field(default_factory=list)
    # populated in __post_init__
    wine_loader: Path | None = None
    wineserver: Path | None = None
    wine_bin: Path | None = None          # user-facing `wine` (script or binary)
    winewrapper: Path | None = None
    templates_dir: Path | None = None     # share/crossover/bottle_templates
    bottle_data_dir: Path | None = None   # share/crossover/bottle_data
    gptk_dir: Path | None = None          # lib64/apple_gptk
    dxvk_dir: Path | None = None
    dxmt_dir: Path | None = None
    moltenvk: Path | None = None
    gstreamer_dir: Path | None = None
    perl_lib: Path | None = None
    build_timestamp: str = ""

    def __post_init__(self) -> None:
        self.bin_dir = self.root / "bin"
        for cand in ("lib64", "lib"):
            d = self.root / cand
            if d.is_dir():
                self.lib_dirs.append(d)
        # cx layouts keep the loader under bin/. Note: *-preloader is only an
        # address-space helper — never a valid WINELOADER on its own.
        for cand in ("wineloader", "wine", "wine64"):
            p = self.bin_dir / cand
            if p.is_file() and self.wine_loader is None:
                self.wine_loader = p
        wb = self.bin_dir / "wine"
        if wb.is_file():
            self.wine_bin = wb
        if self.wine_loader is None:
            # generic builds: bin/wine IS the loader/launcher
            self.wine_loader = self.wine_bin
        ws = self.bin_dir / "wineserver"
        self.wineserver = ws if ws.is_file() else None
        for d in self.dll_search_dirs():
            ww = d / "winewrapper.exe"
            if ww.is_file():
                self.winewrapper = ww
                break
        share = self.root / "share" / "crossover"
        if (share / "bottle_templates").is_dir():
            self.templates_dir = share / "bottle_templates"
        if (share / "bottle_data").is_dir():
            self.bottle_data_dir = share / "bottle_data"
        for lib in self.lib_dirs:
            g = lib / "apple_gptk"
            if g.is_dir():
                self.gptk_dir = g
            for sub in ("dxvk", "dxmt"):
                d = lib / sub
                if d.is_dir():
                    setattr(self, f"{sub}_dir", d)
            mvk = lib / "libMoltenVK.dylib"
            if mvk.is_file():
                self.moltenvk = mvk
            gs = lib / "gstreamer-1.0"
            if gs.is_dir():
                self.gstreamer_dir = gs
        pl = self.root / "lib" / "perl"
        if pl.is_dir():
            self.perl_lib = pl
        if not self.name:
            self.name = self.root.name

    # -- wine internals ----------------------------------------------------
    def unix_lib_wine_dirs(self) -> list[Path]:
        """Directories containing ``ntdll.so`` style unix-side wine libs."""
        dirs = []
        for lib in self.lib_dirs:
            w = lib / "wine"
            for host in ("aarch64-unix", "x86_64-unix", "i386-unix"):
                d = w / host
                if (d / "ntdll.so").is_file():
                    dirs.append(d)
            if (w / "ntdll.so").is_file():
                dirs.append(w)
        return dirs

    def dll_search_dirs(self, winearch: str = "win64") -> list[Path]:
        """WINEDLLPATH entries, mirroring the perl wrapper's construction.

        win64 bottles get ``<host>-windows`` dirs first, then i386-windows,
        then the bare lib/wine dirs so ntdll.so resolves.
        """
        host = "x86_64"
        for lib in self.lib_dirs:
            if (lib / "wine" / "aarch64-unix" / "ntdll.so").exists():
                host = "aarch64"
                break
        dirs: list[Path] = []
        if winearch == "win64":
            for lib in self.lib_dirs:
                d = lib / "wine" / f"{host}-windows"
                if d.is_dir():
                    dirs.append(d)
        for lib in self.lib_dirs:
            d = lib / "wine" / "i386-windows"
            if d.is_dir():
                dirs.append(d)
        for lib in self.lib_dirs:
            d = lib / "wine"
            if d.is_dir():
                dirs.append(d)
        return dirs

    def supports_backend(self, backend: str) -> bool:
        return {
            "d3dmetal": self.gptk_dir is not None,
            "dxvk": self.dxvk_dir is not None and self.moltenvk is not None,
            "dxmt": self.dxmt_dir is not None,
            "wined3d": True,
        }.get(backend, False)

    def available_backends(self) -> list[str]:
        return [b for b in ("d3dmetal", "dxmt", "dxvk", "wined3d")
                if self.supports_backend(b)]

    def templates(self) -> list[str]:
        if self.templates_dir and self.templates_dir.is_dir():
            return sorted(p.name for p in self.templates_dir.iterdir() if p.is_dir())
        return []

    def describe(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "root": str(self.root),
            "version": self.version,
            "backends": self.available_backends(),
            "templates": self.templates(),
            "loader": str(self.wine_loader) if self.wine_loader else None,
        }


def _engine_kind(root: Path) -> str | None:
    if not root.is_dir():
        return None
    if (root / "bin").is_dir() and (root / "share" / "crossover").is_dir():
        return "cx"
    bin_dir = root / "bin"
    if (bin_dir / "wineloader").is_file() or (bin_dir / "wine").is_file():
        return "generic"
    return None


def _cx_conf_values(root: Path) -> dict[str, str]:
    conf = root / "share" / "crossover" / "data" / "CrossOver.conf"
    out: dict[str, str] = {}
    if conf.is_file():
        for line in conf.read_text(errors="replace").splitlines():
            m = re.match(r'\s*"(\w+)"\s*=\s*"([^"]*)"', line)
            if m:
                out[m.group(1)] = m.group(2)
    return out


def _cx_version(root: Path) -> str:
    vals = _cx_conf_values(root)
    if vals.get("ProductVersion"):
        return vals["ProductVersion"]
    plist = root.parent.parent / "Info.plist"  # CX_ROOT -> Contents
    if plist.is_file():
        try:
            with plist.open("rb") as fh:
                info = plistlib.load(fh)
            return str(info.get("CFBundleShortVersionString", ""))
        except Exception:
            pass
    return ""


def _probe_engine(root: Path) -> Engine | None:
    kind = _engine_kind(root)
    if kind is None:
        return None
    eng = Engine(root=root, kind=kind)
    if kind == "cx":
        eng.name = f"crossover ({eng.root})"
        eng.version = _cx_version(root)
        eng.build_timestamp = _cx_conf_values(root).get("BuildTimestamp", "")
    else:
        eng.name = f"{root.name} ({root})"
        # try to learn the version from `wine --version` is too slow here;
        # leave empty - doctor fills it on demand.
    return eng


def _find_engine_root(root: Path, depth: int = 5) -> Path | None:
    """Descend into a tree looking for a directory that looks like a wine
    engine (handles tarballs that nest the engine under Contents/Resources)."""
    if _engine_kind(root) is not None:
        return root
    if depth <= 0 or not root.is_dir():
        return None
    # prefer likely containers first
    children = [c for c in sorted(root.iterdir()) if c.is_dir()
                and not c.name.startswith(".") and not c.name.startswith("._")]
    for child in children:
        found = _find_engine_root(child, depth - 1)
        if found:
            return found
    return None


def discover_engines() -> list[Engine]:
    """Find every usable engine on this machine."""
    engines: list[Engine] = []
    seen: set[str] = set()

    def add(root: Path) -> None:
        try:
            key = str(root.resolve())
        except OSError:
            key = str(root)
        if key in seen:
            return
        eng = _probe_engine(root)
        if eng:
            seen.add(key)
            engines.append(eng)

    # 1. explicit env override
    env = os.environ.get("DECANTER_ENGINE")
    if env:
        add(Path(env).expanduser())

    # 2. engines installed by `decanter engine install`
    if ENGINES_DIR.is_dir():
        for child in sorted(ENGINES_DIR.iterdir()):
            root = _find_engine_root(child)
            if root:
                add(root)

    # 3. CrossOver.app
    for app in CROSSOVER_APPS:
        add(app / "Contents" / "SharedSupport" / "CrossOver")

    # 4. wine on PATH
    for tool in ("wine64", "wine"):
        path = shutil.which(tool)
        if path:
            p = Path(path).resolve()
            # .../bin/wine -> root
            if p.parent.name == "bin":
                add(p.parent.parent)

    # 5. common third-party apps
    for app in (
        Path("/Applications/Wine.app"),
        Path("/Applications/Whisky.app"),
        Path.home() / "Applications/Whisky.app",
    ):
        for sub in (
            "Contents/SharedSupport/CrossOver",
            "Contents/Resources/wine",
            "Contents/SharedSupport/wine",
        ):
            add(app / sub)

    # 6. Whisky-style library engines
    whisky_lib = Path.home() / "Library/Application Support/com.isaacmarovitz.Whisky/Libraries/Wine"
    if whisky_lib.is_dir():
        for child in sorted(whisky_lib.iterdir()):
            add(child)

    return engines


def default_engine() -> Engine | None:
    engines = discover_engines()
    if not engines:
        return None
    # prefer explicitly configured engine
    env = os.environ.get("DECANTER_ENGINE")
    if env:
        want = str(Path(env).expanduser())
        for e in engines:
            if str(e.root) == want:
                return e
    # prefer cx engines (best compatibility), then the most capable
    for e in engines:
        if e.kind == "cx":
            return e
    return max(engines, key=lambda e: len(e.available_backends()))


def get_engine(selector: str | None) -> Engine | None:
    """Resolve an engine by name substring, kind, or path."""
    engines = discover_engines()
    if selector is None:
        return default_engine()
    sel = selector.strip()
    p = Path(sel).expanduser()
    if p.is_dir():
        eng = _probe_engine(p)
        if eng:
            return eng
        # maybe the user pointed at an .app bundle
        for sub in ("Contents/SharedSupport/CrossOver", "Contents/Resources/wine"):
            eng = _probe_engine(p / sub)
            if eng:
                return eng
    low = sel.lower()
    for e in engines:
        if low in e.name.lower() or low == e.kind or low in str(e.root).lower():
            return e
    return None


# ----------------------------------------------------------------------------
# engine install (open-source path)
# ----------------------------------------------------------------------------

def _fetch_text(url: str, timeout: int = 30) -> bytes:
    """Fetch a URL. Uses curl (system trust store) — python.org installs on
    macOS lack root certs out of the box."""
    out = subprocess.run(
        ["curl", "-fsSL", "--max-time", str(timeout), url],
        capture_output=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode(errors="replace").strip()
                           or f"curl exited {out.returncode}")
    return out.stdout


def fetch_open_engine_releases() -> list[dict]:
    """Query the public release feeds for downloadable open wine builds."""
    releases = []
    for feed in OPEN_ENGINE_FEEDS:
        try:
            data = json.loads(_fetch_text(feed).decode())
        except Exception:
            continue
        if isinstance(data, dict):
            data = [data]
        for rel in data:
            assets = [
                {"name": a["name"], "url": a["browser_download_url"],
                 "size": a.get("size", 0)}
                for a in rel.get("assets", [])
                if a["name"].endswith((".tar.xz", ".tar.gz", ".tgz"))
            ]
            if assets:
                releases.append({"tag": rel.get("tag_name", ""),
                                 "assets": assets})
    return releases


def install_engine(url: str, dest_name: str | None = None,
                   progress=print) -> Path:
    """Download and unpack a wine engine tarball into ~/.decanter/engines."""
    ENGINES_DIR.mkdir(parents=True, exist_ok=True)
    name = dest_name or Path(url.split("?")[0]).name.split(".tar")[0]
    dest = ENGINES_DIR / name
    tmp = dest.with_suffix(".downloading")
    progress(f"downloading {url}")
    with tmp.open("wb") as fh:
        fh.write(_fetch_text(url, timeout=600))
    progress(f"unpacking to {dest}")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tmp) as tf:
        tf.extractall(dest, filter="data")
    tmp.unlink()
    # locate the actual engine root inside the extracted tree
    return _find_engine_root(dest) or dest


# ---------------------------------------------------------------------------
# downloadable open graphics payloads (the CrossOver-parity stack)
# ---------------------------------------------------------------------------

def _latest_asset(repo: str, pattern: str) -> dict | None:
    """Find the newest release asset matching `pattern` in a GitHub repo."""
    try:
        rel = json.loads(_fetch_text(
            f"https://api.github.com/repos/{repo}/releases/latest").decode())
    except Exception:
        return None
    for a in rel.get("assets", []):
        if re.search(pattern, a["name"], re.I):
            return {"name": a["name"], "url": a["browser_download_url"],
                    "tag": rel.get("tag_name", "")}
    return None


def _extract_members(tf: tarfile.TarFile, want, dest: Path) -> None:
    for m in tf.getmembers():
        if want(m) and m.isfile():
            m.name = Path(m.name).name
            tf.extract(m, dest, filter="data")


def setup_dxvk(engine: Engine, progress=print) -> bool:
    """Download DXVK + MoltenVK into an open engine so `backend=dxvk` works —
    the open-source equivalent of CrossOver's lib/dxvk + libMoltenVK."""
    lib = engine.root / "lib64"
    if not lib.is_dir():
        lib = engine.root / "lib"
    if not lib.is_dir():
        lib.mkdir(parents=True)

    asset = _latest_asset("doitsujin/dxvk", r"^dxvk-.*\.tar\.gz$")
    if asset is None:
        progress("could not find a DXVK release")
        return False
    progress(f"downloading {asset['name']} ({asset['tag']})")
    import io
    data = _fetch_text(asset["url"], timeout=600)
    dxvk_root = lib / "dxvk"
    (dxvk_root / "x86_64-windows").mkdir(parents=True, exist_ok=True)
    (dxvk_root / "i386-windows").mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        _extract_members(tf, lambda m: re.search(r"/x64/[^/]+\.dll$", m.name),
                         dxvk_root / "x86_64-windows")
        _extract_members(tf, lambda m: re.search(r"/x32/[^/]+\.dll$", m.name),
                         dxvk_root / "i386-windows")
    progress(f"installed dxvk payload -> {dxvk_root}")

    mvk = _latest_asset("KhronosGroup/MoltenVK", r"^MoltenVK-macos\.tar$")
    if mvk is None:
        mvk = _latest_asset("KhronosGroup/MoltenVK", r"macos.*\.(tar|tar\.gz|zip)$")
    if mvk is None:
        progress("could not find a MoltenVK release — dxvk still needs Vulkan")
        return False
    progress(f"downloading {mvk['name']} ({mvk['tag']})")
    data = _fetch_text(mvk["url"], timeout=600)
    dest = lib / "libMoltenVK.dylib"
    if mvk["name"].endswith(".zip"):
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for n in zf.namelist():
                if n.endswith("libMoltenVK.dylib") and "macOS" in n:
                    dest.write_bytes(zf.read(n)); break
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tf:
            _extract_members(tf, lambda m: m.name.endswith(
                "libMoltenVK.dylib") and "macOS" in m.name, lib)
    if not dest.is_file():
        progress("MoltenVK dylib not found in the archive")
        return False
    dest.chmod(0o755)
    for alias in ("libvulkan.1.dylib", "libvulkan.dylib"):
        link = lib / alias
        if not link.exists():
            link.symlink_to("libMoltenVK.dylib")
    progress(f"installed MoltenVK -> {dest}")
    return True
