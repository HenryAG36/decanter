"""Shared helpers for Decanter.

Implements the CrossOver ``cxbottle.conf``-style INI dialect (``"Key" = "value"``
with ``;;`` comments) in a way that round-trips without destroying comments or
formatting, plus small utilities used across the package.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

_LINE_RE = re.compile(r'^\s*"(?P<key>[^"]+)"\s*=\s*"(?P<val>(?:[^"\\]|\\.)*)"\s*$')
_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]+)\]\s*$")
_ENVVAR_RE = re.compile(r"\$\{([^}]+)\}")


def expand_env(value: str, env: dict[str, str] | None = None) -> str:
    """Expand ``${VAR}`` references like CrossOver's expand_string()."""
    env = env or os.environ
    return _ENVVAR_RE.sub(lambda m: env.get(m.group(1), m.group(0)), value)


class CxSection:
    """Ordered key/value map for one INI section."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.fields: dict[str, str] = {}

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.fields.get(key, default)

    def set(self, key: str, value: str) -> None:
        self.fields[key] = value

    def unset(self, key: str) -> None:
        self.fields.pop(key, None)

    def keys(self) -> list[str]:
        return list(self.fields)


class CxConfig:
    """Parser/writer for the ``"Key" = "value"`` INI dialect used by CrossOver.

    Unrecognised or commented lines are preserved verbatim so writes do not
    clobber a file CrossOver can still read.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.sections: dict[str, CxSection] = {}
        # raw lines kept for faithful rewriting
        self._lines: list[str] = []
        if path is not None and Path(path).is_file():
            self.read(path)

    # -- parsing -----------------------------------------------------------
    def read(self, path: str | Path) -> "CxConfig":
        self.path = Path(path)
        self._lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        current: CxSection | None = None
        for line in self._lines:
            m = _SECTION_RE.match(line)
            if m:
                current = self.sections.setdefault(m.group("name"), CxSection(m.group("name")))
                continue
            m = _LINE_RE.match(line)
            if m and current is not None:
                current.set(m.group("key"), m.group("val"))
        return self

    @classmethod
    def loads(cls, text: str) -> "CxConfig":
        obj = cls()
        obj._lines = text.splitlines()
        current = None
        for line in obj._lines:
            m = _SECTION_RE.match(line)
            if m:
                current = obj.sections.setdefault(m.group("name"), CxSection(m.group("name")))
                continue
            m = _LINE_RE.match(line)
            if m and current is not None:
                current.set(m.group("key"), m.group("val"))
        return obj

    # -- access ------------------------------------------------------------
    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        s = self.sections.get(section)
        return s.get(key, default) if s else default

    def set(self, section: str, key: str, value: str) -> None:
        self.sections.setdefault(section, CxSection(section)).set(key, value)

    def unset(self, section: str, key: str) -> None:
        if section in self.sections:
            self.sections[section].unset(key)

    def section(self, name: str, create: bool = True) -> CxSection | None:
        if name in self.sections:
            return self.sections[name]
        if create:
            self.sections[name] = CxSection(name)
            return self.sections[name]
        return None

    # -- serialisation -----------------------------------------------------
    def dumps(self) -> str:
        """Render the file, keeping comment lines and section order when the
        file was parsed; appended keys go at the end of their section."""
        out: list[str] = []
        written: dict[str, set[str]] = {n: set() for n in self.sections}
        section_order: list[str] = []

        if self._lines:
            current: str | None = None
            for line in self._lines:
                m = _SECTION_RE.match(line)
                if m:
                    current = m.group("name")
                    if current not in self.sections:
                        # section was deleted -> drop the header too
                        current = None
                        continue
                    section_order.append(current)
                    out.append(line)
                    continue
                if current is None:
                    out.append(line)
                    continue
                m = _LINE_RE.match(line)
                if m and m.group("key") in self.sections[current].fields:
                    key = m.group("key")
                    out.append(f'"{key}" = "{self.sections[current].fields[key]}"')
                    written[current].add(key)
                elif m and m.group("key") not in self.sections[current].fields:
                    # key was deleted; skip the stale line
                    continue
                else:
                    out.append(line)
            # append keys that were added after parsing
            for name in section_order:
                sec = self.sections[name]
                for key in sec.keys():
                    if key not in written[name]:
                        out.append(f'"{key}" = "{sec.fields[key]}"')
            for name, sec in self.sections.items():
                if name not in section_order:
                    out.append("")
                    out.append(f"[{name}]")
                    for key in sec.keys():
                        out.append(f'"{key}" = "{sec.fields[key]}"')
        else:
            for name, sec in self.sections.items():
                out.append(f"[{name}]")
                for key in sec.keys():
                    out.append(f'"{key}" = "{sec.fields[key]}"')
                out.append("")
        return "\n".join(out) + "\n"

    def write(self, path: str | Path | None = None) -> None:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("no path given")
        target.write_text(self.dumps(), encoding="utf-8")


def quote_arg(arg: str) -> str:
    """Shell-quote a single argument (POSIX)."""
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", arg):
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


def new_bottle_id() -> str:
    return str(uuid.uuid4()).upper()


def unix_to_win_path(path: str) -> str:
    """Convert a macOS path to a ``Z:`` style Wine path."""
    p = str(Path(path).resolve())
    if p == "/":
        return "Z:\\"
    return "Z:" + p.replace("/", "\\")


def win_to_unix_path(bottle_dir: Path, win_path: str) -> Path | None:
    """Convert a ``C:\\...`` / ``Z:\\...`` windows path into the bottle's
    unix filesystem (respecting dosdevices symlinks)."""
    m = re.match(r"^([a-zA-Z]):[\\/](.*)$", win_path)
    if not m:
        return None
    drive, rest = m.group(1).lower(), m.group(2).replace("\\", "/")
    link = bottle_dir / "dosdevices" / f"{drive}:"
    if not link.exists():
        return None
    return link / rest


def is_win_path(value: str) -> bool:
    """Heuristic matching the perl wrapper's is_valid_win_path()."""
    if re.match(r"^[a-zA-Z]:[\\/]", value):
        return True
    if value.startswith("\\\\"):
        return True
    return False


def find_exe_in_drive_c(bottle_dir: Path, name: str) -> Path | None:
    """Search a bottle's drive_c for an executable, mirroring the perl
    wrapper's ``$cx_app`` lookup (depth 3, preferring system32 on ties)."""
    drive_c = bottle_dir / "drive_c"
    if not drive_c.is_dir():
        return None
    base = name.lower()
    if not base.endswith((".exe", ".com")):
        base += ".exe"
    matches: list[Path] = []
    for cand in drive_c.rglob("*"):
        try:
            rel_depth = len(cand.relative_to(drive_c).parts)
        except ValueError:
            continue
        if rel_depth > 4:
            continue
        if cand.name.lower() == base and cand.is_file():
            matches.append(cand)
    if not matches:
        return None
    if len(matches) > 1:
        sys32 = [m for m in matches if "system32" in str(m).lower()]
        if sys32:
            matches = sys32
    return matches[0]


def decode_cxmenu_name(name: str) -> str:
    """Decode cxmenu filename escaping: ``+`` = space, ``^XX`` = hex char."""
    out = []
    i = 0
    while i < len(name):
        c = name[i]
        if c == "+":
            out.append(" ")
            i += 1
        elif c == "^" and i + 2 < len(name):
            try:
                out.append(chr(int(name[i + 1:i + 3], 16)))
            except ValueError:
                out.append(c)
            i += 3
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_url_file(path: Path) -> str | None:
    """Read URL= from a Windows .url (INI) file."""
    try:
        for line in path.read_text(errors="replace").splitlines():
            if line.strip().lower().startswith("url="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def lnk_target(path: Path) -> str | None:
    """Best-effort extraction of a .lnk's Windows target path.

    Parses just enough of the Shell Link format: the first plausible
    ``<drive>:\\...`` ascii path in the LinkInfo / string-data region.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
        return None
    best = None
    for m in re.finditer(rb"([A-Za-z]:\\[ -~]{4,200}?(?:\.exe|\.bat|\.cmd|\.msi))\x00?",
                         data, re.I):
        cand = m.group(1).decode("ascii", errors="replace")
        # prefer paths that look like real fs paths
        if best is None or ("users" not in cand.lower()
                            and "temp" not in cand.lower()):
            best = cand
    return best


def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:3.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}PB"


def dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            pass
    return total
