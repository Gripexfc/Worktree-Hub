#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h}"
APP_PATH="${WORKTREE_HUB_APP_PATH:-$ROOT_DIR/../Worktree Hub.app}"
CONTENTS="$APP_PATH/Contents"

if [[ -d "$APP_PATH" ]]; then
  /bin/rm -rf "$APP_PATH"
fi

/bin/mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources/static"
/usr/bin/xcrun swiftc \
  "$ROOT_DIR/macos/WorktreeHarborApp.swift" \
  -o "$CONTENTS/MacOS/WorktreeHarbor" \
  -framework AppKit -framework WebKit \
  -O

/bin/cp "$ROOT_DIR/macos/Info.plist" "$CONTENTS/Info.plist"
/bin/cp "$ROOT_DIR/worktree_harbor.py" "$CONTENTS/Resources/worktree_harbor.py"
/bin/cp "$ROOT_DIR/static/index.html" "$CONTENTS/Resources/static/index.html"
/bin/cp "$ROOT_DIR/static/styles.css" "$CONTENTS/Resources/static/styles.css"
/bin/cp "$ROOT_DIR/static/priorities.css" "$CONTENTS/Resources/static/priorities.css"
/bin/cp "$ROOT_DIR/static/app.js" "$CONTENTS/Resources/static/app.js"
if [[ -n "${HARBOR_BACKEND_EXECUTABLE:-}" && -x "$HARBOR_BACKEND_EXECUTABLE" ]]; then
  /bin/cp "$HARBOR_BACKEND_EXECUTABLE" "$CONTENTS/Resources/worktree_harbor_backend"
  /bin/chmod +x "$CONTENTS/Resources/worktree_harbor_backend"
fi
/usr/bin/codesign --force --deep --sign - "$APP_PATH"
/usr/bin/touch "$APP_PATH"

echo "$APP_PATH"
