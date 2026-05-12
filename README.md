# Instagram Saved Reels → Markdown Notes Pipeline

Daily automation that scrapes your Instagram saved reels, transcribes them, and produces structured Markdown notes using an LLM API (Mistral, Gemini, or OpenAI).

## How It Works

1. **launchd** triggers `run.sh` daily at 8 AM (fires on next wake if the Mac was asleep)
2. **`scrape_reels.py`** calls the Instagram internal API using cookies from your browser, filters against already-seen reels, and writes a queue of new items
3. For each reel in the queue:
   - **Prepare** (`process_reel.py --prepare`): downloads video via yt-dlp + transcribes with Whisper, or downloads images directly from CDN for photos/carousels
   - **Triage** (`process_reel.py --triage`): LLM classifies whether the content matches your configured topic; non-matching reels are skipped
   - **Analyse** (`process_reel.py --analyse`): LLM generates structured notes → writes one `.md` file per reel
4. Output: `~/Documents/ReelNotes/YYYY-MM-DD_SHORTCODE.md`

Supports videos, photos, and carousels. Already-processed reels are tracked in `state/seen_reels.json` and skipped on future runs.

## Prerequisites

- macOS with [Homebrew](https://brew.sh)
- A supported browser logged into Instagram: [Brave](https://brave.com), [Chrome](https://www.google.com/chrome/), or [Firefox](https://www.mozilla.org/firefox/) (for cookie extraction)
- Python 3.10+
- An LLM API key — Mistral (default), Gemini, or OpenAI

## Setup

**1. Install dependencies:**
```bash
./setup.sh
```

This installs yt-dlp, ffmpeg, openai-whisper, and the required Python packages.

**2. Configure:**
```bash
cp config.env.example config.env
```

Edit `config.env` and fill in:
- `LLM_PROVIDER` — `mistral`, `gemini`, or `openai`
- `LLM_API_KEY` — your API key for the chosen provider
- `INSTAGRAM_BROWSER` — browser to pull cookies from: `brave`, `chrome`, or `firefox` (default: `brave`)
- `OUTPUT_DIR` — where to write `.md` files (default: `~/Documents/ReelNotes`)
- `TOPIC` — what content to keep (default: `"AI, machine learning, or AI-powered tools"`)

**3. Install the daily schedule:**
```bash
./install-launchd.sh
```

Verify it's loaded:
```bash
launchctl list | grep reels
```

## Manual Run

```bash
./run.sh                       # process up to 10 new reels (default)
./run.sh --limit 5             # process at most 5
./run.sh --backfill --limit 20 # collect all unseen reels, not just until the first seen one
```

Watch the log:
```bash
tail -f logs/run_$(date +%Y-%m-%d).log
```

Check output:
```bash
ls ~/Documents/ReelNotes/
```

## Uninstalling the Schedule

```bash
launchctl unload ~/Library/LaunchAgents/com.reels-pipeline.plist
rm ~/Library/LaunchAgents/com.reels-pipeline.plist
```

## Output Format

Each `.md` file contains:
- **Title** (inferred from content)
- **Source URL** + date saved
- **Summary** (2-3 sentences)
- **Full Explanation** with added context
- **Step-by-Step Instructions** (if tutorial/how-to)
- **Key Concepts** with explanations
- **Additional Resources** (links)

## File Structure

```
instagram-saved-analyze/
├── run.sh                      # Pipeline entry point
├── scrape_reels.py             # Instagram API scraper (phase 1)
├── process_reel.py             # Per-reel prepare/triage/analyse (phases 2-4)
├── llm.py                      # LLM abstraction (Mistral / Gemini / OpenAI)
├── pipeline.py                 # Helpers: state management, markdown writer
├── config.env                  # Your settings (gitignored — copy from .example)
├── config.env.example          # Config template
├── setup.sh                    # One-time dependency install
├── requirements.txt            # Python dependencies
├── install-launchd.sh          # Generates and loads the launchd daily schedule
├── com.reels-pipeline.plist    # launchd schedule template
├── templates/
│   └── analysis_prompt.txt     # Prompt template for LLM analysis
├── state/
│   └── seen_reels.json         # Tracks processed reels (gitignored)
└── logs/                       # Daily run logs (gitignored)
```

## Troubleshooting

**"No Instagram sessionid cookie found"**
→ Open your browser and log into Instagram manually. Sessions expire periodically.

**yt-dlp download failures**
→ The pipeline retries with browser cookies. If it still fails, run `brew upgrade yt-dlp` — Instagram changes their protocol frequently.

**Rate limit errors (429) from LLM**
→ The pipeline retries with exponential backoff. If you hit daily limits, wait until midnight (UTC) for the free tier to reset, or switch to a different provider in `config.env`.

**Reprocessing a reel**
→ Remove its entry from `state/seen_reels.json` and run again.

**Too many reels skipped by triage**
→ Adjust the `TOPIC` value in `config.env` to broaden the filter. The LLM leans toward `true` when in doubt.

**Whisper transcription accuracy**
→ Edit the `--model base` flag in `process_reel.py` → change to `small` or `medium` for better accuracy (slower).
