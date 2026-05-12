#!/usr/bin/env python3
"""
Per-reel processor. Called by run.sh for each new reel.

Modes:
  --prepare   Download video + transcribe audio → writes /tmp/reel_SHORTCODE/ready.json
  --triage    LLM classifies AI vs non-AI. Exit 0=AI, Exit 2=not AI, Exit 1=error
  --analyse   LLM analyses content → writes .md + updates state

Usage:
    python3 process_reel.py --prepare --shortcode ABC123 --url https://...
    python3 process_reel.py --triage  --shortcode ABC123
    python3 process_reel.py --analyse --shortcode ABC123
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).parent
TEMPLATES_DIR = PROJECT_DIR / "templates"
OUTPUT_DIR = Path.home() / "Documents" / "ReelNotes"
LOG_DIR = PROJECT_DIR / "logs"

TRIAGE_PROMPT = """\
You are a content classifier. Given a short-form video's caption and transcript, \
decide if it is meaningfully about: {topic}

Answer with a single JSON object: {{"is_match": true}} or {{"is_match": false}}

Caption:
{caption}

Transcript:
{transcript}

Rules:
- true: the video is primarily about the topic above
- false: the topic is only briefly mentioned, or the video is about something else entirely
- When genuinely in doubt, lean true
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str, shortcode: str = ""):
    timestamp = datetime.now().strftime("%H:%M:%S")
    prefix = f"[{shortcode}] " if shortcode else ""
    print(f"{timestamp} {prefix}{msg}", flush=True)


def run(cmd: list[str], check: bool = True, capture: bool = True,
        timeout: int = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout)


# ── Step 1: Fetch metadata + caption via yt-dlp ───────────────────────────────

def get_caption(url: str, shortcode: str) -> str:
    log("Fetching caption via yt-dlp...", shortcode)
    try:
        result = run(["yt-dlp", "--dump-json", "--no-download", url], timeout=60)
        meta = json.loads(result.stdout)
        caption = meta.get("description") or meta.get("title") or "[No caption]"
        return caption.strip()
    except Exception as e:
        log(f"Caption fetch failed: {e} — using placeholder", shortcode)
        return "[No caption]"


# ── Step 2: Download video ────────────────────────────────────────────────────

def download_video(url: str, output_path: Path, shortcode: str) -> bool:
    log("Downloading video...", shortcode)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    for attempt, extra in enumerate([[], ["--cookies-from-browser", "brave"]], start=1):
        try:
            run(["yt-dlp", *extra, "-o", str(output_path), url], timeout=120)
            if output_path.exists():
                log(f"Downloaded ({output_path.stat().st_size // 1024} KB)", shortcode)
                return True
        except subprocess.CalledProcessError as e:
            log(f"Download attempt {attempt} failed: {e.stderr[:200]}", shortcode)

    return False


# ── Step 3: Extract frames ────────────────────────────────────────────────────

def extract_frames(video_path: Path, frames_dir: Path, count: int, shortcode: str) -> list[Path]:
    log(f"Extracting {count} frames...", shortcode)
    result = run([
        "python3", str(PROJECT_DIR / "pipeline.py"), "extract-frames",
        "--video", str(video_path),
        "--output-dir", str(frames_dir),
        "--count", str(count),
    ])
    paths = [Path(p) for p in json.loads(result.stdout)]
    log(f"Extracted {len(paths)} frames", shortcode)
    return paths


# ── Step 4: Transcribe audio ──────────────────────────────────────────────────

def transcribe(video_path: Path, output_dir: Path, shortcode: str) -> str:
    log("Transcribing audio (whisper base)...", shortcode)
    try:
        run(["whisper", str(video_path),
             "--model", "base",
             "--output_format", "txt",
             "--output_dir", str(output_dir)])

        txt_path = output_dir / (video_path.stem + ".txt")
        if txt_path.exists():
            transcript = txt_path.read_text().strip()
            if transcript:
                log(f"Transcript: {len(transcript)} chars", shortcode)
                return transcript
    except Exception as e:
        log(f"Whisper failed: {e}", shortcode)

    return "[No audio/speech detected]"


# ── Step 5: Analyse via Claude API ────────────────────────────────────────────

def analyse(transcript: str, caption: str, url: str,
            frame_paths: list[Path], shortcode: str) -> str:
    import anthropic  # only needed when API credits are available
    log("Analysing with Claude API...", shortcode)

    template = (TEMPLATES_DIR / "analysis_prompt.txt").read_text()
    prompt = (template
              .replace("{transcript}", transcript)
              .replace("{caption}", caption)
              .replace("{url}", url))

    # Build message content: frames first, then the text prompt
    content: list = []
    for frame_path in frame_paths:
        if frame_path.exists():
            image_data = base64.standard_b64encode(frame_path.read_bytes()).decode()
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": image_data,
                },
            })
    content.append({"type": "text", "text": prompt})

    client = anthropic.Anthropic()

    # Stream to avoid timeouts on long responses
    with client.messages.stream(
        model=MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    ) as stream:
        response_text = stream.get_final_message()

    result = next(
        (block.text for block in response_text.content if block.type == "text"), ""
    )
    log(f"Analysis complete ({len(result)} chars)", shortcode)
    return result


# ── Step 6: Parse analysis sections ──────────────────────────────────────────

def parse_sections(analysis: str) -> dict:
    """Split the Claude response on ### headers into a dict."""
    sections = {
        "title": "Untitled Reel",
        "summary": "",
        "explanation": "",
        "steps": "N/A — not a tutorial.",
        "key_concepts": "",
        "tools": "",
        "resources": "",
    }

    header_map = {
        "title": "title",
        "summary": "summary",
        "full explanation": "explanation",
        "step-by-step instructions": "steps",
        "key concepts": "key_concepts",
        "tools & repositories": "tools",
        "additional resources": "resources",
    }

    current_key = None
    buffer = []

    for line in analysis.splitlines():
        if line.startswith("###"):
            # Save previous section
            if current_key:
                sections[current_key] = "\n".join(buffer).strip()
            # Find new section key
            heading = line.lstrip("#").strip().lower()
            current_key = next(
                (v for k, v in header_map.items() if k in heading), None
            )
            buffer = []
        elif current_key:
            buffer.append(line)

    if current_key and buffer:
        sections[current_key] = "\n".join(buffer).strip()

    return sections


# ── Step 7: Write markdown ────────────────────────────────────────────────────

def write_markdown(sections: dict, url: str, shortcode: str, date: str,
                   output_dir: Path = None) -> Path:
    if output_dir is None:
        output_dir = OUTPUT_DIR
    output_path = output_dir / f"{date}_{shortcode}.md"
    payload = {
        "title": sections["title"],
        "source_url": url,
        "date_saved": date,
        "summary": sections["summary"],
        "explanation": sections["explanation"],
        "steps": sections["steps"],
        "key_concepts": sections["key_concepts"],
        "tools": sections["tools"],
        "resources": sections["resources"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["python3", str(PROJECT_DIR / "pipeline.py"), "write-markdown",
         "--output", str(output_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )
    out = json.loads(proc.stdout)
    log(f"Wrote {out['path']}", shortcode)
    return Path(out["path"])


# ── Step 8: Update state ──────────────────────────────────────────────────────

def update_state(url: str, title: str, date: str, shortcode: str):
    run([
        "python3", str(PROJECT_DIR / "pipeline.py"), "save-state",
        "--url", url,
        "--title", title,
        "--date", date,
    ])
    log("State updated", shortcode)


# ── Triage mode (Phase 3): AI vs non-AI classifier ───────────────────────────

def triage_llm(shortcode: str) -> bool:
    """Return True if the reel matches the configured topic. Exits 1 on error."""
    import llm

    ready_path = Path(f"/tmp/reel_{shortcode}/ready.json")
    if not ready_path.exists():
        log(f"ERROR: ready.json not found at {ready_path}", shortcode)
        sys.exit(1)

    ready = json.loads(ready_path.read_text())
    topic = os.environ.get("TOPIC", "AI, machine learning, or AI-powered tools")
    prompt = TRIAGE_PROMPT.format(
        topic=topic,
        caption=ready.get("caption", "[No caption]"),
        transcript=ready.get("transcript", "[No transcript]"),
    )

    image_paths = ready.get("image_paths", [])
    try:
        text = llm.generate(prompt, json_mode=True, image_paths=image_paths or None)
        result = json.loads(text)
        is_match = result.get("is_match", False)
        log(f"Triage: {'match ✓' if is_match else 'skip ✗'}", shortcode)
        return is_match
    except Exception as e:
        log(f"ERROR: Triage failed: {e}", shortcode)
        sys.exit(1)


# ── Tool URL resolution (post-analysis) ──────────────────────────────────────

def search_tool_url(tool_name: str) -> str | None:
    """Search DuckDuckGo for a tool's GitHub repo or official site."""
    import re
    import requests
    from urllib.parse import quote_plus, unquote

    for query in [f"{tool_name} github", tool_name]:
        try:
            resp = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )},
                timeout=10,
            )
            if not resp.ok:
                continue
            # DDG embeds result URLs as uddg= params in redirect links
            urls = [unquote(u) for u in re.findall(r"uddg=([^&\"']+)", resp.text)]
            for url in urls[:5]:
                if any(url.startswith(p) for p in ("http://", "https://")):
                    if not any(d in url for d in ("duckduckgo.com", "bing.com", "google.com")):
                        return url
        except Exception:
            pass
    return None


def resolve_tool_links(tools_text: str, shortcode: str) -> str:
    """For tools listed without a URL, search DuckDuckGo and fill in the link."""
    import re
    lines = tools_text.split("\n")
    resolved = []
    for line in lines:
        # Matches `- **Name** — desc` (bold without a hyperlink)
        m = re.match(r"^(-\s+)\*\*([^*]+)\*\*(.*)", line)
        if m:
            prefix, name, rest = m.group(1), m.group(2).strip(), m.group(3)
            log(f"Searching for: {name}", shortcode)
            url = search_tool_url(name)
            if url:
                line = f"{prefix}[{name}]({url}){rest}"
                log(f"  → {url}", shortcode)
            else:
                log(f"  → not found", shortcode)
        resolved.append(line)
    return "\n".join(resolved)


# ── Analyse mode (Phase 4): full LLM analysis → .md ──────────────────────────

def analyse_llm(shortcode: str):
    """Call the configured LLM with the pre-built prompt, parse sections, write .md."""
    import llm

    ready_path = Path(f"/tmp/reel_{shortcode}/ready.json")
    if not ready_path.exists():
        log(f"ERROR: ready.json not found at {ready_path}", shortcode)
        sys.exit(1)

    ready = json.loads(ready_path.read_text())
    provider = os.environ.get("LLM_PROVIDER", "mistral")
    log(f"Analysing with {provider}...", shortcode)

    image_paths = ready.get("image_paths", [])
    try:
        analysis_text = llm.generate(ready["analysis_prompt"], image_paths=image_paths or None)
    except Exception as e:
        log(f"ERROR: LLM analysis failed: {e}", shortcode)
        sys.exit(1)

    log(f"Analysis received ({len(analysis_text)} chars)", shortcode)

    sections = parse_sections(analysis_text)
    if sections.get("tools"):
        sections["tools"] = resolve_tool_links(sections["tools"], shortcode)
    date = ready["date"]
    output_dir = Path(os.environ.get("OUTPUT_DIR", "~/Documents/ReelNotes")).expanduser()
    md_path = write_markdown(sections, ready["url"], shortcode, date, output_dir)
    update_state(ready["url"], sections["title"], date, shortcode)

    shutil.rmtree(f"/tmp/reel_{shortcode}", ignore_errors=True)
    log(f"Done → {md_path}", shortcode)


# ── Prepare mode (Phase 2): download + transcribe + frames ───────────────────

def download_images(shortcode: str, output_dir: Path) -> list[Path]:
    """Download images from CDN URLs stored in /tmp/reel_SHORTCODE/image_urls.json.
    Converts HEIC → JPEG since vision APIs don't support HEIC."""
    import browser_cookie3
    import requests as req

    urls_file = Path(f"/tmp/reel_{shortcode}/image_urls.json")
    if not urls_file.exists():
        log(f"ERROR: image_urls.json not found at {urls_file}", shortcode)
        return []

    image_urls = json.loads(urls_file.read_text())
    if not image_urls:
        log("No image URLs found", shortcode)
        return []

    output_dir.mkdir(parents=True, exist_ok=True)

    browser = os.environ.get("INSTAGRAM_BROWSER", "brave")
    jar = getattr(browser_cookie3, browser)(domain_name=".instagram.com")
    session = req.Session()
    session.cookies = jar
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    images: list[Path] = []
    for i, url in enumerate(image_urls):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            # Determine extension from Content-Type or URL
            ct = resp.headers.get("Content-Type", "image/jpeg")
            ext = {"image/jpeg": ".jpg", "image/png": ".png",
                   "image/webp": ".webp", "image/heic": ".heic"}.get(ct.split(";")[0], ".jpg")
            path = output_dir / f"img_{i:03d}{ext}"
            path.write_bytes(resp.content)

            # Convert HEIC → JPEG (vision APIs don't support HEIC)
            if path.suffix.lower() in (".heic", ".heif"):
                jpg = path.with_suffix(".jpg")
                run(["sips", "-s", "format", "jpeg", str(path), "--out", str(jpg)], timeout=30)
                path.unlink()
                path = jpg

            # Resize to max 1024px wide to reduce token cost (quarter the data, same content)
            try:
                run(["sips", "-Z", "1024", str(path)], timeout=15)
            except Exception:
                pass  # sips not available or failed — use original size

            images.append(path)
        except Exception as e:
            log(f"Failed to download image {i}: {e}", shortcode)

    log(f"Downloaded {len(images)}/{len(image_urls)} image(s)", shortcode)
    return images


def prepare(shortcode: str, url: str, media_type: int = 2):
    """Download + transcribe (video) or download images (photo/carousel). Write ready.json."""
    tmp_dir = Path(f"/tmp/reel_{shortcode}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    log(f"Preparing: {url} (type={'video' if media_type == 2 else 'photo/carousel'})", shortcode)

    caption = get_caption(url, shortcode)

    if media_type == 2:
        # ── Video: download + transcribe ──────────────────────────────────────
        video_path = tmp_dir / "video.mp4"
        if not download_video(url, video_path, shortcode):
            log("ERROR: Download failed", shortcode)
            sys.exit(1)
        transcript = transcribe(video_path, tmp_dir, shortcode)
        image_paths = []
    else:
        # ── Photo / carousel: download images from CDN ────────────────────────
        images = download_images(shortcode, tmp_dir / "images")
        if not images:
            log("ERROR: No images downloaded", shortcode)
            sys.exit(1)
        transcript = "[Visual content — see images]"
        image_paths = [str(p) for p in images]

    # Build analysis prompt
    date = datetime.now().strftime("%Y-%m-%d")
    template = (TEMPLATES_DIR / "analysis_prompt.txt").read_text()
    analysis_prompt = (template
                       .replace("{transcript}", transcript)
                       .replace("{caption}", caption)
                       .replace("{url}", url))
    ready = {
        "shortcode": shortcode,
        "url": url,
        "caption": caption,
        "transcript": transcript,
        "image_paths": image_paths,   # non-empty for photos/carousels
        "date": date,
        "analysis_prompt": analysis_prompt,
    }
    ready_path = tmp_dir / "ready.json"
    ready_path.write_text(json.dumps(ready, indent=2))
    log(f"Ready: {ready_path} ({len(image_paths)} images)", shortcode)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Process a single Instagram reel")
    parser.add_argument("--shortcode", required=True)
    parser.add_argument("--url", help="Required for --prepare mode")
    parser.add_argument("--media-type", type=int, default=2,
                        help="1=photo, 2=video, 8=carousel (default: 2)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare",  action="store_true", help="Download + transcribe → ready.json")
    mode.add_argument("--triage",   action="store_true", help="LLM AI/non-AI classifier (exit 0=AI, 2=not AI)")
    mode.add_argument("--analyse",  action="store_true", help="Full LLM analysis → .md + state update")
    args = parser.parse_args()

    shortcode = args.shortcode

    if args.prepare:
        if not args.url:
            parser.error("--url is required with --prepare")
        prepare(shortcode, args.url, media_type=args.media_type)
        return

    if args.triage:
        is_match = triage_llm(shortcode)
        sys.exit(0 if is_match else 2)

    if args.analyse:
        analyse_llm(shortcode)
        return

    # Full mode (API analysis) — kept for future use if API credits become available
    date = datetime.now().strftime("%Y-%m-%d")
    tmp_dir = Path(f"/tmp/reel_{shortcode}")

    log(f"Starting: {url}", shortcode)

    try:
        caption = get_caption(url, shortcode)

        video_path = tmp_dir / "video.mp4"
        if not download_video(url, video_path, shortcode):
            log("ERROR: Download failed — skipping reel", shortcode)
            sys.exit(1)

        frames_dir = tmp_dir / "frames"
        frame_paths = extract_frames(video_path, frames_dir, count=3, shortcode=shortcode)

        transcript = transcribe(video_path, tmp_dir, shortcode)

        analysis = analyse(transcript, caption, url, frame_paths, shortcode)

        sections = parse_sections(analysis)
        md_path = write_markdown(sections, url, shortcode, date)

        # 7. Update state (only after successful write)
        update_state(url, sections["title"], date, shortcode)

        log(f"Done → {md_path}", shortcode)

    except Exception as e:
        log(f"ERROR: Unhandled exception: {e}", shortcode)
        sys.exit(1)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
