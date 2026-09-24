#!/bin/zsh
set -euo pipefail

ROOT_DIR="${0:A:h}"
APP_PATH="${WORKTREE_HUB_APP_PATH:-$ROOT_DIR/../Worktree Hub.app}"
ARCH_NAME="${WORKTREE_HUB_ARCH:-$(uname -m)}"
OUTPUT_PATH="${WORKTREE_HUB_DMG_PATH:-$ROOT_DIR/../Worktree-Hub-macOS-$ARCH_NAME.dmg}"
STAGING_DIR="$(mktemp -d)"
trap '/bin/rm -rf "$STAGING_DIR"' EXIT

if [[ ! -d "$APP_PATH" ]]; then
  echo "Application not found: $APP_PATH" >&2
  exit 1
fi

/bin/cp -R "$APP_PATH" "$STAGING_DIR/Worktree Hub.app"
/bin/ln -s /Applications "$STAGING_DIR/Applications"
/bin/rm -f "$OUTPUT_PATH"
/usr/bin/hdiutil create -volname "Worktree Hub" -srcfolder "$STAGING_DIR" -ov -format UDZO "$OUTPUT_PATH"
echo "$OUTPUT_PATH"
