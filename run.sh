#!/bin/bash
# Daily Instagram Reels Pipeline
#
# Phase 1 — Scrape:   pure Python — reads Brave cookies → IG API → writes queue.json
# Phase 2 — Prepare:  pure Python per reel (yt-dlp + whisper)
# Phase 3 — Triage:   Gemini Flash classifies AI vs non-AI (exit 2 = skip)
# Phase 4 — Analyse:  Gemini Flash full analysis → writes .md + updates state
#
# Usage: ./run.sh [--limit N] [--backfill]
#   --limit N    process at most N reels this run (default: 10)
#   --backfill   collect ALL unseen reels across all pages (use when catching up)
#
# Examples:
#   ./run.sh                       # daily run, max 10 reels
#   ./run.sh --backfill --limit 15 # backfill: 15 per run, repeat until caught up

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Load user config (required — copy config.env.example → config.env to set up)
if [ -f "$SCRIPT_DIR/config.env" ]; then
  set -o allexport
  source "$SCRIPT_DIR/config.env"
  set +o allexport
else
  echo "ERROR: config.env not found. Copy config.env.example → config.env and fill in your settings."
  exit 1
fi

DATE=$(date +%Y-%m-%d)
LOG="$SCRIPT_DIR/logs/run_${DATE}.log"
QUEUE="/tmp/reels_queue.json"
LIMIT="${DAILY_LIMIT:-10}"   # from config.env, default 10
BACKFILL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit)    LIMIT="$2"; shift 2 ;;
    --backfill) BACKFILL=1; shift ;;
    *) shift ;;
  esac
done

mkdir -p "$SCRIPT_DIR/logs"
echo "=== Pipeline started at $(date) ===" >> "$LOG"

# ── Phase 1: Scrape (pure Python — zero Claude tokens) ───────────────────────
echo "--- Phase 1: Scraping ---" >> "$LOG"
SCRAPE_FLAGS=""
[ "$BACKFILL" -eq 1 ] && SCRAPE_FLAGS="--no-early-stop"

rm -f "$QUEUE"  # always start fresh; stale queue from failed prior run causes reprocessing
python3 "$SCRIPT_DIR/scrape_reels.py" $SCRAPE_FLAGS >> "$LOG" 2>&1

if [ ! -f "$QUEUE" ]; then
  echo "ERROR: Queue file not created. Aborting." >> "$LOG"
  echo "=== Pipeline aborted at $(date) ===" >> "$LOG"
  exit 1
fi

QUEUE_COUNT=$(python3 -c "import json; print(len(json.load(open('$QUEUE'))))" 2>/dev/null || echo 0)
echo "INFO: $QUEUE_COUNT new reels queued" >> "$LOG"

if [ "$QUEUE_COUNT" -eq 0 ]; then
  echo "INFO: Nothing to do." >> "$LOG"
  echo "=== Pipeline finished at $(date) ===" >> "$LOG"
  exit 0
fi

# ── Phase 2–4: Per-reel loop ──────────────────────────────────────────────────
echo "--- Phases 2-4: Processing $QUEUE_COUNT reels ---" >> "$LOG"

SUCCESS=0
FAIL=0
SKIPPED=0

while IFS='|' read -r shortcode url media_type; do
  echo "INFO: [$shortcode] Starting... (type=$media_type)" >> "$LOG"

  # For photos/carousels: write image URLs to temp file before prepare
  if [ "$media_type" != "2" ]; then
    mkdir -p "/tmp/reel_$shortcode"
    python3 -c "
import json
q = json.load(open('$QUEUE'))
for item in q:
    if item['shortcode'] == '$shortcode':
        json.dump(item.get('image_urls', []),
                  open('/tmp/reel_$shortcode/image_urls.json', 'w'))
        break
" 2>> "$LOG"
  fi

  # Phase 2: prepare (download + transcribe/images) — pure Python
  python3 "$SCRIPT_DIR/process_reel.py" --prepare \
    --shortcode "$shortcode" --url "$url" --media-type "$media_type" \
    >> "$LOG" 2>&1

  if [ $? -ne 0 ]; then
    echo "ERROR: [$shortcode] Prepare failed — skipping" >> "$LOG"
    FAIL=$((FAIL + 1))
    continue
  fi

  # Phase 3: triage — Gemini Flash AI/non-AI classifier
  python3 "$SCRIPT_DIR/process_reel.py" --triage \
    --shortcode "$shortcode" \
    >> "$LOG" 2>&1

  TRIAGE_CODE=$?
  if [ $TRIAGE_CODE -eq 2 ]; then
    echo "INFO: [$shortcode] TRIAGE_SKIP (not AI-related)" >> "$LOG"
    rm -rf "/tmp/reel_$shortcode"
    # Mark as seen so it's never re-downloaded or re-triaged
    python3 "$SCRIPT_DIR/pipeline.py" save-state \
      --url "$url" --title "[TRIAGE_SKIP]" --date "$(date +%Y-%m-%d)" >> "$LOG" 2>&1 || true
    SKIPPED=$((SKIPPED + 1))
    continue
  elif [ $TRIAGE_CODE -ne 0 ]; then
    echo "ERROR: [$shortcode] Triage error — skipping (will retry next run)" >> "$LOG"
    rm -rf "/tmp/reel_$shortcode"
    FAIL=$((FAIL + 1))
    continue
  fi

  # Phase 4: analyse — Gemini Flash full analysis → .md + state
  python3 "$SCRIPT_DIR/process_reel.py" --analyse \
    --shortcode "$shortcode" \
    >> "$LOG" 2>&1

  EXIT_CODE=$?

  if [ $EXIT_CODE -eq 0 ]; then
    SUCCESS=$((SUCCESS + 1))
    echo "INFO: [$shortcode] ✓" >> "$LOG"
    # Safety net: mark as seen in case analyse didn't reach save-state
    python3 "$SCRIPT_DIR/pipeline.py" save-state \
      --url "$url" --title "$shortcode" --date "$(date +%Y-%m-%d)" >> "$LOG" 2>&1 || true
  else
    FAIL=$((FAIL + 1))
    echo "ERROR: [$shortcode] ✗ (exit $EXIT_CODE)" >> "$LOG"
  fi

done < <(python3 -c "
import json, sys
items = json.load(open('$QUEUE'))
limit = int('$LIMIT')
if limit > 0:
    items = items[:limit]
for item in items:
    print(item['shortcode'] + '|' + item['url'] + '|' + str(item.get('media_type', 2)))
")

rm -f "$QUEUE"
echo "=== Pipeline finished at $(date) — Success: $SUCCESS  Skipped: $SKIPPED  Failed: $FAIL ===" >> "$LOG"
