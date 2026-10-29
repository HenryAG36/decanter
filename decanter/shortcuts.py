"""Generate double-clickable .app wrappers for Windows programs."""

from __future__ import annotations

import os
import stat
from pathlib import Path

_LAUNCHER_SH = """\
#!/bin/bash
exec "{decanter_bin}" run --bottle {bottle} -- {target} "$@"
"""

_INFO_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIdentifier</key><string>app.decanter.{bundle_id}</string>
    <key>CFBundleName</key><string>{name}</string>
    <key>CFBundleDisplayName</key><string>{name}</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def make_app(name: str, bottle_name: str, target: str,
             dest_dir: Path | None = None, decanter_bin: str = "decanter",
             icon: Path | None = None) -> Path:
    """Create ``~/Applications/<name>.app`` which runs ``target`` in the
    given bottle."""
    dest_dir = dest_dir or (Path.home() / "Applications")
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name)
    app = dest_dir / f"{safe}.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    (app / "Contents" / "Resources").mkdir(exist_ok=True)

    quoted_target = target if target.startswith('"') else f'"{target}"'
    launcher = macos / "launcher"
    launcher.write_text(_LAUNCHER_SH.format(
        decanter_bin=decanter_bin,
        bottle=f'"{bottle_name}"',
        target=quoted_target,
    ))
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP
                   | stat.S_IXOTH)

    bundle_id = "".join(c if c.isalnum() else "-" for c in safe.lower())
    (app / "Contents" / "Info.plist").write_text(
        _INFO_PLIST.format(name=safe, bundle_id=bundle_id))
    if icon and icon.is_file():
        import shutil
        shutil.copy2(icon, app / "Contents" / "Resources" / "app.icns")
    return app
