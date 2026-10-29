"""Extract icons from Windows executables — PE resource parsing only,
stdlib-only, mirroring how CrossOver's icon grid shows real app icons.

Reads the .rsrc section, rebuilds an .ico from RT_GROUP_ICON + RT_ICON
entries, converts the largest frame (PNG or 32bpp BMP) to PNG, and caches
it under ~/.decanter/icons/.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ICON_DIR = Path.home() / ".decanter" / "icons"


def _png(width: int, height: int, rgba: bytes) -> bytes:
    """Wrap raw RGBA rows (top-down, with filter byte 0) into a PNG."""
    raw = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4]
                   for y in range(height))
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _rsrc_section(pe: bytes) -> bytes | None:
    if len(pe) < 0x40 or pe[:2] != b"MZ":
        return None
    off = struct.unpack_from("<I", pe, 0x3C)[0]
    if pe[off:off + 4] != b"PE\0\0":
        return None
    nsec = struct.unpack_from("<H", pe, off + 6)[0]
    opt = struct.unpack_from("<H", pe, off + 20)[0]
    sec = off + 24 + opt
    for i in range(nsec):
        base = sec + i * 40
        name = pe[base:base + 8].rstrip(b"\0")
        vsize, vaddr, rsize, raddr = struct.unpack_from("<IIII", pe, base + 8)
        if name == b".rsrc":
            return pe[raddr:raddr + rsize], raddr, vaddr
    return None


def _walk_dir(rsrc: bytes, node: int, depth: int = 0):
    """Yield (type, id/name, child_offset, is_dir) at each level."""
    n_named, n_id = struct.unpack_from("<HH", rsrc, node + 12)
    count = n_named + n_id
    for i in range(count):
        e = node + 16 + i * 8
        ident, child = struct.unpack_from("<II", rsrc, e)
        yield (ident & 0xFFFF, ident & 0x80000000 != 0,
               child & 0x7FFFFFFF, child & 0x80000000 != 0)


def _data_entry(rsrc: bytes, node: int, vaddr: int, raw: int):
    rva, size = struct.unpack_from("<II", rsrc, node)
    return rva - vaddr + raw, size


def _res_at(rsrc: bytes, want_type: int, vaddr: int, raw: int):
    """Return {name_id: (file_offset, size)} for a resource type."""
    out = {}
    for ident, _, child, is_dir in _walk_dir(rsrc, 0):
        if ident != want_type or not is_dir:
            continue
        for name_id, _, child2, is_dir2 in _walk_dir(rsrc, child):
            if not is_dir2:
                continue
            for lang, _, de, is_dir3 in _walk_dir(rsrc, child2):
                if is_dir3:
                    continue
                out[name_id] = _data_entry(rsrc, de, vaddr, raw)
                break
    return out


def exe_to_png(exe: Path) -> bytes | None:
    """Best-effort: biggest icon frame from a PE -> PNG bytes."""
    try:
        pe = exe.read_bytes()
    except OSError:
        return None
    rsrc_info = _rsrc_section(pe)
    if not rsrc_info:
        return None
    rsrc, raw, vaddr = rsrc_info
    groups = _res_at(rsrc, 14, vaddr, raw)   # RT_GROUP_ICON
    icons = _res_at(rsrc, 3, vaddr, raw)     # RT_ICON
    if not groups:
        return None
    # first group; its GRPICONDIR lists (w,h,colors,res,planes,bpp,size,resid)
    gid = sorted(groups)[0]
    off, size = groups[gid]
    g = pe[off:off + size]
    if len(g) < 6:
        return None
    n = struct.unpack_from("<H", g, 4)[0]
    frames = []
    for i in range(n):
        w, h, _c, _r, _pl, _bpp, sz, rid = struct.unpack_from("<BBBBHHIH", g, 6 + i * 14)
        frames.append((w or 256, h or 256, sz, rid))
    frames.sort(reverse=True)
    for w, h, sz, rid in frames:
        ent = icons.get(rid)
        if not ent:
            continue
        o, s = ent
        data = pe[o:o + s]
        png = _ico_frame_to_png(data, w, h)
        if png:
            return png
    return None


def _ico_frame_to_png(data: bytes, w: int, h: int) -> bytes | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    if len(data) < 40:
        return None
    dib = struct.unpack_from("<IiiHHIIiiII", data, 0)
    width, height2, planes, bpp = dib[1], dib[2], dib[3], dib[4]
    height = abs(height2) // 2 or h
    if width <= 0:
        width = w
    # XOR bitmap: BGRA bottom-up
    xor_size = width * height * (bpp // 8)
    pixels = data[40:40 + xor_size]
    if bpp != 32 or len(pixels) < xor_size:
        return None
    rgba = bytearray(width * height * 4)
    for y in range(height):
        src = (height - 1 - y) * width * 4
        row = pixels[src:src + width * 4]
        for x in range(width * 4):
            rgba[y * width * 4 + x] = row[x] if x % 4 != 3 else row[x]
        # swap B->R
        for x in range(width):
            i = y * width * 4 + x * 4
            rgba[i], rgba[i + 2] = rgba[i + 2], rgba[i]
    # AND mask -> alpha where image has none
    and_off = 40 + xor_size
    stride = ((width + 31) // 32) * 4
    if len(data) >= and_off + stride * height:
        mask = data[and_off:and_off + stride * height]
        has_alpha = any(rgba[i + 3] for i in range(0, len(rgba), 4))
        if not has_alpha:
            for y in range(height):
                row = (height - 1 - y) * stride
                for x in range(width):
                    if mask[row + x // 8] & (0x80 >> (x % 8)):
                        rgba[(y * width + x) * 4 + 3] = 0
                    else:
                        rgba[(y * width + x) * 4 + 3] = 255
    return _png(width, height, bytes(rgba))


def icon_for(exe_path: str | Path | None, key: str) -> str | None:
    """Cached PNG path for an exe's icon, keyed by program name."""
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in key)
    out = ICON_DIR / f"{safe}.png"
    if out.is_file():
        return str(out)
    if not exe_path:
        return None
    unix = Path(exe_path)
    if not unix.is_file():
        return None
    png = exe_to_png(unix)
    if not png:
        return None
    out.write_bytes(png)
    return str(out)
