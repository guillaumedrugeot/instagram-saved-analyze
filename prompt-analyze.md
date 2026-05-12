# Analyze One Reel via claude.ai

You have ONE job: open claude.ai, upload the prepared transcript and frames,
submit the analysis prompt, read the response, write the .md file, update state.
Do not navigate to Instagram. Do not download anything. Exit when done.

The shortcode and URL are at the bottom of this prompt.

---

## Step 1: Read the prepared data

Run via Bash:
```
cat /tmp/reel_SHORTCODE/ready.json
```
(replace SHORTCODE with the actual value)

Parse the JSON to get: `analysis_prompt`, `url`, `date`.
The `analysis_prompt` field is the complete, ready-to-send prompt — no substitution needed.

---

## Step 2: The analysis prompt is already built

Use the `analysis_prompt` field from `ready.json` exactly as-is.
Do NOT read the template file. Do NOT do any substitution.

---

## Step 3: Open claude.ai

1. Use `mcp__Claude_in_Chrome__switch_browser` to switch to Brave.
2. Use `mcp__Claude_in_Chrome__tabs_create_mcp` to open a new tab.
   **Important**: note the `tabId` value returned — you will need it in Step 5 to close this tab.
3. Use `mcp__Claude_in_Chrome__navigate` to go to `https://claude.ai/new`.
4. Wait 3 seconds via `mcp__Claude_in_Chrome__javascript_tool`:
   `await new Promise(r => setTimeout(r, 3000))`

---

## Step 4: Submit prompt

1. Use `mcp__Claude_in_Chrome__find` to locate the chat input.
2. Use `mcp__Claude_in_Chrome__computer` (action: type) to type the filled prompt.
3. Press Enter: `mcp__Claude_in_Chrome__computer` (action: keypress, key: Return).

---

## Step 5: Wait for and read the response

Poll every 5 seconds (up to 120 seconds) via `mcp__Claude_in_Chrome__javascript_tool`:
```javascript
!!document.querySelector('[data-testid="stop-button"]')
```
When it returns `false`, the response is ready.

Use `mcp__Claude_in_Chrome__get_page_text` to extract the full response text.
Close the tab using `mcp__Claude_in_Chrome__tabs_close_mcp` with the `tabId` you noted in Step 3.

If claude.ai is unreachable or times out: log the error and EXIT with status 1.

---

## Step 6: Write the markdown file

Parse the response into sections by splitting on `###` headers.
Write a JSON file then pipe it to the markdown writer via Bash:
```
cat > /tmp/reel_SHORTCODE/analysis.json << 'EOF'
{ "title": "...", "source_url": "URL", "date_saved": "DATE",
  "summary": "...", "explanation": "...", "steps": "...",
  "key_concepts": "...", "resources": "..." }
EOF

cat /tmp/reel_SHORTCODE/analysis.json | \
  python3 ~/instagram-saved-analyze/pipeline.py write-markdown \
    --output ~/Documents/ReelNotes/DATE_SHORTCODE.md
```

Verify the file was created. If not: EXIT with status 1.

---

## Step 7: Update state and clean up

Only run this after Step 6 succeeds:
```
python3 ~/instagram-saved-analyze/pipeline.py save-state \
  --url "URL" --title "TITLE" --date "DATE"
```

Then clean up:
```
rm -rf /tmp/reel_SHORTCODE
```

---

## Reel to process
