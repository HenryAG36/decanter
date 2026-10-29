#!/bin/bash
# Build Decanter.app — native SwiftUI frontend bundled with the python backend.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
APP="$ROOT/build/Decanter.app"
SDK="$(xcrun --show-sdk-path)"
SWIFTC="$(xcrun -f swiftc)"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/python"
cp Info.plist "$APP/Contents/"

# bundle the python backend (pure stdlib — runs on /usr/bin/python3)
cp -R "$ROOT/decanter" "$APP/Contents/Resources/python/"

"$SWIFTC" -O -sdk "$SDK" -target arm64-apple-macos14.0 \
    -framework SwiftUI -framework AppKit -framework UniformTypeIdentifiers \
    Sources/*.swift -o "$APP/Contents/MacOS/Decanter" -parse-as-library 2>&1 |
    grep -v "^$" >&2 || true

# fail if binary wasn't produced
test -x "$APP/Contents/MacOS/Decanter"

codesign --force --deep --sign - "$APP" 2>/dev/null || true
echo "built: $APP"
