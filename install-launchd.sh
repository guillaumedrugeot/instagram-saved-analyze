#!/bin/bash
# Installs the launchd agent that runs the reels pipeline daily at 8 AM.
# Run once after setup: ./install-launchd.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.reels-pipeline.plist"
PLIST_NAME="com.reels-pipeline.plist"
DEST="$HOME/Library/LaunchAgents/$PLIST_NAME"

if [ ! -f "$PLIST_SRC" ]; then
  echo "ERROR: $PLIST_SRC not found. Run from the reels directory."
  exit 1
fi

sed \
  -e "s|__REELS_DIR__|$SCRIPT_DIR|g" \
  -e "s|__HOME__|$HOME|g" \
  "$PLIST_SRC" > "$DEST"

launchctl load "$DEST"
echo "Installed: $DEST"
echo "Verify: launchctl list | grep reels"
