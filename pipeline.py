#!/usr/bin/env python3
"""Helper utilities for the Instagram Reels pipeline.

Called by Claude Code via Bash. Each subcommand is independent,
outputs JSON to stdout, errors to stderr.
"""

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

STATE_FILE = Path(__file__).parent / "state" / "seen_reels.json"


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"processed": []}
    with open(STATE_FILE) as f:
        return json.load(f)


def _save_state_atomic(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        json.dump(state, f, indent=2)
        fcntl.flock(f, fcntl.LOCK_UN)
    tmp.replace(STATE_FILE)


# --- Subcommands ---


def cmd_check_new(args):
    """Exit 0 if URL is new (not processed), exit 1 if already seen."""
    state = _load_state()
    seen_urls = {entry["url"] for entry in state["processed"]}
    if args.url in seen_urls:
        sys.exit(1)
    sys.exit(0)


def cmd_save_state(args):
    """Append a processed reel entry to seen_reels.json."""
    state = _load_state()
    state["processed"].append({
        "url": args.url,
        "title": args.title,
        "date": args.date,
        "processed_at": datetime.now().isoformat(),
    })
    _save_state_atomic(state)
    print(json.dumps({"status": "saved", "url": args.url}))


def cmd_extract_frames(args):
    """Extract N evenly-spaced frames from a video using ffmpeg."""
    video = Path(args.video)
    output_dir = Path(args.output_dir)
    count = args.count

    if not video.exists():
        print(f"Video not found: {video}", file=sys.stderr)
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Get video duration via ffprobe
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(video)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"ffprobe failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    probe = json.loads(result.stdout)
    duration = float(probe["format"]["duration"])

    # Calculate timestamps for evenly-spaced frames
    if duration <= 0:
        print("Video has zero duration", file=sys.stderr)
        sys.exit(1)

    frames = []
    for i in range(count):
        timestamp = (duration / (count + 1)) * (i + 1)
        output_path = output_dir / f"frame_{i + 1:03d}.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(timestamp),
                "-i", str(video),
                "-frames:v", "1",
                "-q:v", "2",
                str(output_path),
            ],
            capture_output=True,
        )
        if output_path.exists():
            frames.append(str(output_path))

    if not frames:
        print("No frames extracted", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(frames))


def cmd_write_markdown(args):
    """Read JSON from stdin, write formatted .md file."""
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    title = data.get("title", "Untitled Reel")
    source_url = data.get("source_url", "")
    date_saved = data.get("date_saved", datetime.now().strftime("%Y-%m-%d"))
    summary = data.get("summary", "")
    explanation = data.get("explanation", "")
    steps = data.get("steps", "")
    key_concepts = data.get("key_concepts", "")
    tools = data.get("tools", "")
    resources = data.get("resources", "")

    tools_section = f"\n## Tools & Repositories\n\n{tools}\n" if tools.strip() else ""

    md = f"""# {title}

**Source:** {source_url}
**Date Saved:** {date_saved}

## Summary

{summary}

## Full Explanation

{explanation}

## Step-by-Step Instructions

{steps}

## Key Concepts

{key_concepts}
{tools_section}
## Additional Resources

{resources}
"""

    output.write_text(md)
    print(json.dumps({"status": "written", "path": str(output)}))


def main():
    parser = argparse.ArgumentParser(description="Instagram Reels pipeline helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # check-new
    p_check = subparsers.add_parser("check-new", help="Check if URL is new")
    p_check.add_argument("--url", required=True)
    p_check.set_defaults(func=cmd_check_new)

    # save-state
    p_save = subparsers.add_parser("save-state", help="Save processed reel to state")
    p_save.add_argument("--url", required=True)
    p_save.add_argument("--title", required=True)
    p_save.add_argument("--date", required=True)
    p_save.set_defaults(func=cmd_save_state)

    # extract-frames
    p_frames = subparsers.add_parser("extract-frames", help="Extract frames from video")
    p_frames.add_argument("--video", required=True)
    p_frames.add_argument("--output-dir", required=True)
    p_frames.add_argument("--count", type=int, default=4)
    p_frames.set_defaults(func=cmd_extract_frames)

    # write-markdown
    p_md = subparsers.add_parser("write-markdown", help="Write markdown from JSON stdin")
    p_md.add_argument("--output", required=True)
    p_md.set_defaults(func=cmd_write_markdown)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
