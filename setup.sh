#!/bin/bash
set -e

echo "=== Instagram Reels Pipeline Setup ==="

# Install system dependencies via Homebrew
echo ""
echo "Installing yt-dlp and ffmpeg..."
brew install yt-dlp ffmpeg 2>/dev/null || echo "Already installed or brew not available"

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
pip3 install --break-system-packages -r "$(dirname "$0")/requirements.txt" 2>/dev/null || \
  pip3 install -r "$(dirname "$0")/requirements.txt" 2>/dev/null || \
  echo "WARNING: pip install failed. Try: pip3 install -r requirements.txt"

# Create output directory
echo ""
echo "Creating directories..."
mkdir -p ~/Documents/ReelNotes
mkdir -p "$(dirname "$0")/state"
mkdir -p "$(dirname "$0")/logs"
mkdir -p "$(dirname "$0")/templates"

# Initialize state file
STATE_FILE="$(dirname "$0")/state/seen_reels.json"
if [ ! -f "$STATE_FILE" ]; then
  echo '{"processed": []}' > "$STATE_FILE"
  echo "Initialized $STATE_FILE"
else
  echo "State file already exists: $STATE_FILE"
fi

# Make scripts executable
chmod +x "$(dirname "$0")/run.sh" 2>/dev/null || true
chmod +x "$(dirname "$0")/install-launchd.sh" 2>/dev/null || true

# Verify dependencies
echo ""
echo "=== Verification ==="
echo -n "yt-dlp:   " && (which yt-dlp && yt-dlp --version) || echo "NOT FOUND"
echo -n "ffmpeg:   " && which ffmpeg || echo "NOT FOUND"
echo -n "ffprobe:  " && which ffprobe || echo "NOT FOUND"
echo -n "whisper:  " && which whisper || echo "NOT FOUND"
echo -n "python3:  " && which python3 || echo "NOT FOUND"
echo -n "Browser:  " && (
  [ -d "/Applications/Brave Browser.app" ] && echo "Brave" ||
  [ -d "/Applications/Google Chrome.app" ] && echo "Chrome" ||
  [ -d "/Applications/Firefox.app" ] && echo "Firefox" ||
  echo "NOT FOUND (Brave, Chrome, or Firefox required)"
)

echo ""
echo "=== Setup complete ==="
echo "Next steps:"
echo "  1. cp config.env.example config.env  →  fill in your LLM_API_KEY and INSTAGRAM_BROWSER"
echo "  2. Ensure you're logged into Instagram in your browser (Brave, Chrome, or Firefox)"
echo "  3. Run a manual test: ./run.sh"
echo "  4. Set up the daily schedule: ./install-launchd.sh"
