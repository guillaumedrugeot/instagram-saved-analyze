#!/usr/bin/env python3
"""
Scrape Instagram saved reels — zero Claude tokens.

Extracts cookies directly from Brave, calls the Instagram internal API,
filters against seen_reels.json, writes /tmp/reels_queue.json.

Usage:
    python3 scrape_reels.py
Exit codes:
    0 — queue written (may be empty if no new reels)
    1 — fatal error (not logged in, cookies unavailable, etc.)
"""

import json
import os
import sys
import time
from pathlib import Path

import browser_cookie3
import requests

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR   = Path(__file__).parent
STATE_FILE    = PROJECT_DIR / "state" / "seen_reels.json"
QUEUE_FILE    = Path("/tmp/reels_queue.json")
API_URL       = "https://www.instagram.com/api/v1/feed/saved/posts/"
MAX_TO_QUEUE  = 200   # cap at 200 most-recently saved
PAUSE_BETWEEN = 1.0   # seconds between paginated requests (be polite)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _shortcode_from_url(url: str) -> str:
    """Extract the shortcode from any Instagram URL (/p/, /reel/, etc.)."""
    return url.rstrip("/").split("/")[-1]


def load_seen() -> set[str]:
    """Return a set of shortcodes (IDs) already processed, regardless of URL format."""
    if not STATE_FILE.exists():
        return set()
    data = json.loads(STATE_FILE.read_text())
    return {_shortcode_from_url(entry["url"]) for entry in data.get("processed", [])}


def get_cookies() -> requests.cookies.RequestsCookieJar:
    """Extract Instagram cookies from the configured browser via browser_cookie3."""
    browser = os.environ.get("INSTAGRAM_BROWSER", "brave").lower()
    extractors = {
        "brave":   browser_cookie3.brave,
        "chrome":  browser_cookie3.chrome,
        "firefox": browser_cookie3.firefox,
    }
    if browser not in extractors:
        print(f"ERROR: Unknown INSTAGRAM_BROWSER={browser!r}. Choose: {list(extractors)}")
        sys.exit(1)
    try:
        jar = extractors[browser](domain_name=".instagram.com")
        # Verify we actually got a session cookie
        names = {c.name for c in jar}
        if "sessionid" not in names:
            print(f"ERROR: No Instagram sessionid cookie found in {browser}.")
            print(f"       Make sure you are logged into Instagram in {browser}.")
            sys.exit(1)
        return jar
    except Exception as e:
        print(f"ERROR: Could not read {browser} cookies: {e}")
        print(f"       Is {browser} installed? Try closing {browser} and retrying.")
        sys.exit(1)


def get_csrf(jar: requests.cookies.RequestsCookieJar) -> str:
    return next((c.value for c in jar if c.name == "csrftoken"), "")


# ── Core scrape ───────────────────────────────────────────────────────────────

def fetch_new_reels(jar, csrf: str, seen: set[str],
                    early_stop: bool = True) -> list[dict]:
    """
    early_stop=True  (daily): stop as soon as we hit a seen reel.
                     Works because Instagram returns newest-first and daily
                     runs are fully caught up — the first seen reel means
                     everything after it is also already processed.

    early_stop=False (backfill): collect all unseen reels across all pages,
                     skipping seen ones without stopping. Needed when only
                     a partial batch was processed in a prior run.
    """
    session = requests.Session()
    session.cookies = jar
    session.headers.update({
        "X-CSRFToken":   csrf,
        "X-IG-App-ID":   "936619743392459",
        "Referer":       "https://www.instagram.com/",
        "User-Agent":    (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    })

    new_reels = []
    next_max_id = None

    while len(new_reels) < MAX_TO_QUEUE:
        url = API_URL + (f"?max_id={requests.utils.quote(next_max_id)}" if next_max_id else "")

        resp = session.get(url, timeout=30)
        if resp.status_code == 401:
            print("ERROR: Instagram returned 401 — session expired. Log in again in Brave.")
            sys.exit(1)
        if not resp.ok:
            print(f"ERROR: Instagram API returned {resp.status_code}: {resp.text[:200]}")
            sys.exit(1)

        data = resp.json()

        if data.get("status") != "ok":
            print(f"ERROR: API status not ok: {data}")
            sys.exit(1)

        items = data.get("items", [])
        if not items:
            break

        hit_seen = False
        for item in items:
            media = item.get("media", {})
            code = media.get("code")
            if not code:
                continue
            media_type = media.get("media_type")
            # media_type: 1=photo, 2=video/reel, 8=carousel (photos or videos)
            if media_type not in (1, 2, 8):
                print(f"SKIP [{code}] media_type={media_type} (unknown type)")
                continue
            type_label = {1: "photo", 2: "video", 8: "carousel"}.get(media_type)
            print(f"QUEUE [{code}] {type_label}")
            url_path = "reel" if media_type == 2 else "p"
            reel_url = f"https://www.instagram.com/{url_path}/{code}/"
            if code in seen:
                if early_stop:
                    hit_seen = True  # daily mode: stop here
                    break
                else:
                    continue        # backfill mode: skip and keep going
            # For photos/carousels, capture CDN image URLs now (they expire, but we
            # process within seconds of scraping so they'll still be valid)
            image_urls = []
            if media_type == 1:
                candidates = media.get("image_versions2", {}).get("candidates", [])
                if candidates:
                    image_urls = [candidates[0]["url"]]
            elif media_type == 8:
                for cm in media.get("carousel_media", []):
                    candidates = cm.get("image_versions2", {}).get("candidates", [])
                    if candidates:
                        image_urls.append(candidates[0]["url"])

            new_reels.append({
                "shortcode": code,
                "url": reel_url,
                "media_type": media_type,
                "image_urls": image_urls,
            })
            if len(new_reels) >= MAX_TO_QUEUE:
                break

        if hit_seen or not data.get("more_available") or not data.get("next_max_id"):
            break

        next_max_id = data["next_max_id"]
        time.sleep(PAUSE_BETWEEN)

    return new_reels


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-early-stop", action="store_true",
                        help="Collect all unseen reels across all pages (use for backfill)")
    args = parser.parse_args()

    seen   = load_seen()
    jar    = get_cookies()
    csrf   = get_csrf(jar)
    reels  = fetch_new_reels(jar, csrf, seen, early_stop=not args.no_early_stop)

    QUEUE_FILE.write_text(json.dumps(reels, indent=2))
    print(f"Queued {len(reels)} new reels → {QUEUE_FILE}")


if __name__ == "__main__":
    main()
