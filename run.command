#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
exec /usr/bin/python3 "$SCRIPT_DIR/worktree_harbor.py"
