"""Bottle management — CrossOver-compatible Wine prefixes.

A *bottle* is a directory containing ``system.reg`` (a Wine prefix) plus a
``cxbottle.conf`` in CrossOver's INI dialect. Bottles created by Decanter are
fully interoperable with CrossOver itself: they show up in CrossOver's GUI
when placed in its Bottles directory, and Decanter can drive bottles that
CrossOver created.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

from .util import CxConfig, new_bottle_id

CROSSOVER_USER_DIR = Path.home() / "Library/Application Support/CrossOver"
DEFAULT_PRIVATE_ROOTS = [
    CROSSOVER_USER_DIR / "Bottles",
    Path(os.environ.get("DECANTER_HOME", Path.home() / ".decanter")) / "bottles",
]
MANAGED_ROOTS = [Path("/Library/Application Support/CrossOver/Bottles")]

# Windows-version -> (template, winearch, winver) — mirrors
# share/crossover/bottle_templates/*.
TEMPLATES: dict[str, tuple[str, str]] = {
    "win98": ("win98", "win32"),
    "win2000": ("win2000", "win32"),
    "winxp": ("winxp", "win32"),
    "winxp_64": ("winxp", "win64"),
    "winvista": ("vista", "win32"),
    "winvista_64": ("vista", "win64"),
    "win7": ("win7", "win32"),
    "win7_64": ("win7", "win64"),
    "win8": ("win8", "win32"),
    "win8_64": ("win8", "win64"),
    "win10": ("win10", "win32"),
    "win10_64": ("win10", "win64"),
    "win11_64": ("win11", "win64"),
}
DEFAULT_TEMPLATE = "win10_64"


def bottle_roots() -> list[Path]:
    """Ordered directories searched for private bottles."""
    roots: list[Path] = []
    extra = os.environ.get("DECANTER_BOTTLE_PATH") or os.environ.get("CX_BOTTLE_PATH")
    if extra:
        roots.extend(Path(p) for p in extra.split(":") if p)
    roots.extend(DEFAULT_PRIVATE_ROOTS)
    out: list[Path] = []
    for r in roots:
        if r not in out:
            out.append(r)
    return out


def default_create_root(prefer_crossover: bool = True) -> Path:
    """Where new bottles go. When the CrossOver bottles dir exists (or the
    cx engine is in use) we create there so bottles appear in CrossOver too."""
    if prefer_crossover and DEFAULT_PRIVATE_ROOTS[0].parent.is_dir():
        DEFAULT_PRIVATE_ROOTS[0].mkdir(parents=True, exist_ok=True)
        return DEFAULT_PRIVATE_ROOTS[0]
    root = DEFAULT_PRIVATE_ROOTS[1]
    root.mkdir(parents=True, exist_ok=True)
    return root


def valid_bottle_name(name: str) -> bool:
    return bool(name) and "/" not in name and name not in (".", "..")


@dataclass
class Bottle:
    """A wine prefix + its cxbottle.conf."""

    path: Path
    name: str
    config: CxConfig

    @property
    def conf_path(self) -> Path:
        return self.path / "cxbottle.conf"

    @property
    def exists(self) -> bool:
        return self.path.is_dir()

    @property
    def valid(self) -> bool:
        return (self.path / "system.reg").is_file()

    @property
    def winearch(self) -> str:
        return self.config.get("Bottle", "WineArch", "win64") or "win64"

    @property
    def template(self) -> str:
        return self.config.get("Bottle", "Template", "") or ""

    @property
    def drive_c(self) -> Path:
        return self.path / "drive_c"

    def env_vars(self) -> dict[str, str]:
        """[EnvironmentVariables] as a dict (values unexpanded)."""
        sec = self.config.section("EnvironmentVariables", create=False)
        return dict(sec.fields) if sec else {}

    def set_env(self, key: str, value: str | None) -> None:
        if value is None:
            self.config.unset("EnvironmentVariables", key)
        else:
            self.config.set("EnvironmentVariables", key, value)

    def save_config(self) -> None:
        self.config.write(self.conf_path)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "template": self.template,
            "winearch": self.winearch,
            "version": self.config.get("Bottle", "Version", "") or "",
            "env": self.env_vars(),
        }


def find_bottle(name: str, create_root: Path | None = None) -> Bottle | None:
    """Resolve a bottle by name or absolute path."""
    if name.startswith("/"):
        p = Path(name)
        if (p / "system.reg").is_file() or p.is_dir():
            cfg = CxConfig(p / "cxbottle.conf")
            return Bottle(path=p, name=p.name, config=cfg)
        return None
    for root in bottle_roots():
        p = root / name
        if (p / "system.reg").is_file():
            return Bottle(path=p, name=name, config=CxConfig(p / "cxbottle.conf"))
    if create_root is not None:
        p = create_root / name
        return Bottle(path=p, name=name, config=CxConfig())
    return None


def list_bottles() -> list[Bottle]:
    bottles: list[Bottle] = []
    seen: set[str] = set()
    for root in bottle_roots() + MANAGED_ROOTS:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda c: c.name.lower()):
            if not child.is_dir() or child.name in seen:
                continue
            if (child / "system.reg").is_file():
                seen.add(child.name)
                bottles.append(Bottle(child, child.name, CxConfig(child / "cxbottle.conf")))
    return bottles


def default_bottle_link(root: Path) -> Path:
    return root / "default"


def get_default_bottle() -> Bottle | None:
    for root in bottle_roots():
        link = default_bottle_link(root)
        if link.is_symlink() or link.is_dir():
            target = Path(os.path.realpath(link)) if link.is_symlink() else link
            if (target / "system.reg").is_file():
                return Bottle(target, target.name, CxConfig(target / "cxbottle.conf"))
    return None


def set_default_bottle(bottle: Bottle) -> None:
    root = bottle.path.parent
    link = default_bottle_link(root)
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(bottle.path.name)


# ----------------------------------------------------------------------------
# cxbottle.conf generation — CrossOver-compatible
# ----------------------------------------------------------------------------

_BOTTLE_CONF_TEMPLATE = """\
;;----------------------------------------------------------------------
;;
;;  CrossOver Bottle Configuration File (written by decanter)
;;
;;----------------------------------------------------------------------
[Bottle]
"BottleID" = "{bottle_id}"
"Version" = "{version}"
"Timestamp" = "{timestamp}"
"Encoding" = "UTF-8"
"Template" = "{template}"
"MenuRoot" = "/Windows Applications"
"MenuStrip" = "1"
"MenuMode" = "ignore"
"AssocMode" = "ignore"
"WineArch" = "{winearch}"
"Decanter" = "{decanter_version}"

[EnvironmentVariables]
;;"PROMPT" = "$p$g"
"""


def write_new_bottle_conf(bottle_dir: Path, template: str, winearch: str,
                          engine_version: str = "", timestamp: str | None = None,
                          decanter_version: str = "") -> None:
    conf = _BOTTLE_CONF_TEMPLATE.format(
        bottle_id=new_bottle_id(),
        version=engine_version,
        timestamp=timestamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
        template=template,
        winearch=winearch,
        decanter_version=decanter_version,
    )
    (bottle_dir / "cxbottle.conf").write_text(conf, encoding="utf-8")


def write_system_reg_stub(bottle_dir: Path, winearch: str) -> None:
    (bottle_dir / "system.reg").write_text(
        f"WINE REGISTRY Version 2\n#arch={winearch}\n", encoding="utf-8"
    )


# ----------------------------------------------------------------------------
# bottle operations
# ----------------------------------------------------------------------------

def delete_bottle(bottle: Bottle) -> None:
    shutil.rmtree(bottle.path)


def copy_bottle(src: Bottle, new_name: str, root: Path | None = None) -> Bottle:
    if not valid_bottle_name(new_name):
        raise ValueError(f"invalid bottle name {new_name!r}")
    dest_root = root or src.path.parent
    dest = dest_root / new_name
    if dest.exists():
        raise FileExistsError(f"bottle {new_name!r} already exists")
    shutil.copytree(src.path, dest, symlinks=True)
    # give the copy its own identity
    cfg = CxConfig(dest / "cxbottle.conf")
    cfg.set("Bottle", "BottleID", new_bottle_id())
    cfg.write(dest / "cxbottle.conf")
    return Bottle(dest, new_name, cfg)


def rename_bottle(bottle: Bottle, new_name: str) -> Bottle:
    if not valid_bottle_name(new_name):
        raise ValueError(f"invalid bottle name {new_name!r}")
    dest = bottle.path.parent / new_name
    if dest.exists():
        raise FileExistsError(f"bottle {new_name!r} already exists")
    bottle.path.rename(dest)
    return Bottle(dest, new_name, CxConfig(dest / "cxbottle.conf"))


def archive_bottle(bottle: Bottle, out: Path) -> Path:
    """Create a .tar.gz archive of the bottle (like `cxbottle --tar`)."""
    with tarfile.open(out, "w:gz") as tf:
        tf.add(bottle.path, arcname=bottle.name)
    return out


def _restore_member(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo:
    """tarfile filter for bottle archives.

    Wine prefixes *must* keep absolute symlinks (``dosdevices/d:``,
    ``dosdevices/y:`` -> $HOME, stub links into the engine dir), so the
    ``data`` filter is too strict. We still reject member names that
    escape the destination and drop setuid/setgid/ownership metadata.
    """
    name = member.name
    if name.startswith("/") or ".." in Path(name).parts:
        raise tarfile.TarError(f"unsafe path in archive: {name!r}")
    # relative link targets must not escape the extraction root
    if member.islnk() and not member.linkname.startswith("/"):
        resolved = (Path(member.name).parent / member.linkname)
        if ".." in resolved.parts:
            raise tarfile.TarError(f"unsafe hardlink target: {member.linkname!r}")
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    if member.isfile():
        member.mode &= 0o777
    return member


def restore_bottle(archive: Path, root: Path | None = None,
                   name: str | None = None) -> Bottle:
    dest_root = root or default_create_root()
    dest_root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tf:
        tf.extractall(dest_root, filter=_restore_member)
        top = tf.getnames()[0].split("/")[0]
    extracted = dest_root / top
    if name and name != top:
        final = dest_root / name
        if final.exists():
            raise FileExistsError(f"bottle {name!r} already exists")
        extracted.rename(final)
        extracted = final
    return Bottle(extracted, extracted.name, CxConfig(extracted / "cxbottle.conf"))
