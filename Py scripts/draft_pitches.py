"""
draft_pitches.py

Reads the Curators and Blogs tabs from the pipeline Google Sheet, applies
a matching/skip pipeline, drafts a pitch for each eligible row via the
Claude API, and writes the result into the Review Queue tab with
Status = "pending".

This script NEVER sends anything. It only drafts and queues. Sending is a
separate script (Gmail/SMTP side) that only ever touches rows marked
"approved" by you.

MATCHING PIPELINE (in order, each step can disqualify a row):
  1. Genre match      - does the campaign genre appear in the row's Genre field?
  2. Subgenre match    - if genre didn't match, does it appear in Subgenre?
  3. Outreach Status    - has this song already been pitched to this row?
  4. Contact Status     - is the contact info known-dead ("Defunct")?
  5. Similar Artists    - does the row's "Notable Artists Covered" field
                          mention anyone in your manually maintained
                          SIMILAR_ARTISTS_REFERENCE? If so, that gets passed
                          to the drafting prompt as extra context.

Steps 1-4 are hard gates (fail any one -> row is skipped, no API call spent).
Step 5 is soft -- it never disqualifies a row, it only enriches the prompt
if a match is found.

Run this after:
  - Blogs tab is populated with real data (columns below)
  - Curators tab is populated with real data
  - Pitch Templates tab has real template content (a curator-family row and
    a blog-family row, at minimum -- tagged via a "Best For" column)
  - Review Queue tab exists with columns:
    Date | Type | Target | Draft Text | Status | Notes

Expected Blogs tab columns (matches the real workbook, not a placeholder):
  Blog/Publication Name | Genre | Subgenre | Notable Artists Covered |
  Language | Country | Submission Method | Contact Info | Contact Status |
  Source URL | Status Notes | Outreach Status

Setup:
  pip install gspread google-auth anthropic
  Place your Google service account JSON at the path in SHEET_CREDENTIALS_PATH
  Set your Claude API key as the ANTHROPIC_API_KEY environment variable
"""

import os
import sys
import json
import time
import datetime

import gspread
from google.oauth2.service_account import Credentials
from anthropic import Anthropic

# --- Configuration ---------------------------------------------------------

# UPDATE THIS once the xlsx is converted to a live Google Sheet -- the
# converted file gets a NEW id, different from the original xlsx's id.
SHEET_ID = "1zQTh1KvorgbCykJ5NhxBdRaiUE987qjlXK8q1VvyamA"

SHEET_CREDENTIALS_PATH = os.path.expanduser("~/promo-pipeline/sheet_credentials.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MAX_TOKENS = 400

# Between-call delay, mainly relevant once this scales past a handful of rows
DRAFT_DELAY_SECONDS = 1.5

# --- Per-campaign config: loaded from an external file, NOT hardcoded ------
# This is the whole point of the plug-and-play design: this script never
# changes between releases. What changes is which config file you point it
# at. Cowork (or you by hand) writes a fresh JSON file per song using
# campaign_config.example.json as the template, and this script just reads
# whatever path it's given.
#
# Usage:
#   python3 draft_pitches.py                      -> uses CAMPAIGN_CONFIG_PATH above
#   python3 draft_pitches.py /path/to/song2.json  -> overrides it for this run only

# *** THE ONE LINE TO CHANGE FOR EACH NEW RELEASE ***
# Set this to the path of that song's campaign config JSON. Everything else
# in the script stays untouched between releases -- just edit this one
# variable, save, and run. (A CLI argument, if given, still overrides this.)

#_____________VVVVV UPDATE THIS FILE FOR EACH NEW RELEASE!!!!!VVVVVVV______________________
CAMPAIGN_CONFIG_PATH = "JSON/nicolas-cage.json" 

REQUIRED_CONFIG_KEYS = ["title", "artist", "genre", "subgenre", "link"]


def load_campaign_config(path=None):
    if path is None:
        path = CAMPAIGN_CONFIG_PATH

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No campaign config found at '{path}'. Copy "
            f"campaign_config.example.json, fill it in for this release, "
            f"and save it there (or pass a path as the first CLI argument)."
        )

    with open(path, "r") as f:
        config = json.load(f)

    missing = [k for k in REQUIRED_CONFIG_KEYS if not config.get(k) or config[k] == "REPLACE_ME"]
    if missing:
        raise ValueError(
            f"Campaign config at '{path}' is missing or has placeholder "
            f"values for: {', '.join(missing)}. Fill these in before running."
        )

    return config

ANTI_FABRICATION_RULE = (
    "Only use facts explicitly provided to you below. Never invent or imply "
    "streaming numbers, chart positions, press mentions, follower counts, or "
    "any kind of momentum/traction claim that was not given to you. If you "
    "are unsure whether something was given to you, leave it out entirely."
)


def get_clients():
    creds = Credentials.from_service_account_file(SHEET_CREDENTIALS_PATH, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID)
    claude = Anthropic()  # reads ANTHROPIC_API_KEY from environment
    return sheet, claude


def load_rows(sheet, tab_name):
    try:
        ws = sheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        print(f"[skip] Tab '{tab_name}' not found -- skipping this source.")
        return []
    return ws.get_all_records()


def load_templates(sheet):
    """Returns dict keyed by template 'Best For' family, e.g. 'curator' / 'blog'."""
    rows = load_rows(sheet, "Pitch Templates")
    templates = {}
    for row in rows:
        family = str(row.get("Best For", "")).strip().lower()
        if family:
            templates[family] = row
    return templates


def _contains(haystack, needle):
    """Case-insensitive substring check, tolerant of empty/missing cells."""
    if not haystack or not needle:
        return False
    return needle.strip().lower() in str(haystack).strip().lower()


def check_blog_eligibility(row, campaign):
    """
    Runs the full matching pipeline for a single Blogs-tab row.
    `campaign` is the dict loaded from the campaign config JSON.
    Returns a dict: {eligible: bool, reason: str, matched_artists: list}
    """
    genre_field = row.get("Genre", "")
    subgenre_field = row.get("Subgenre", "")
    outreach_status = str(row.get("Outreach Status", "")).lower()
    contact_status = str(row.get("Contact Status", "")).lower()
    notable_artists = row.get("Notable Artists Covered", "")

    similar_artists_reference = campaign.get("similar_artists_reference", {})

    # Step 1: genre match
    genre_hit = _contains(genre_field, campaign["genre"])

    # Step 2: subgenre fallback, only checked if genre didn't hit
    subgenre_hit = False
    if not genre_hit:
        subgenre_hit = _contains(subgenre_field, campaign["subgenre"])

    if not genre_hit and not subgenre_hit:
        return {"eligible": False, "reason": "no genre/subgenre match", "matched_artists": []}

    # Step 3: already contacted for this song?
    if "already contacted" in outreach_status or "sent" in outreach_status:
        return {"eligible": False, "reason": "already contacted", "matched_artists": []}

    # Step 4: contact info known dead?
    if "defunct" in contact_status:
        return {"eligible": False, "reason": "contact defunct", "matched_artists": []}

    # Step 5 (soft): similar-artist reference lookup -- never disqualifies
    matched_artists = [
        name for name in similar_artists_reference
        if _contains(notable_artists, name)
    ]

    matched_via = "genre" if genre_hit else "subgenre"
    return {"eligible": True, "reason": f"matched via {matched_via}", "matched_artists": matched_artists}


def is_curator_eligible(row):
    """Deterministic skip check for the Curators tab -- unchanged logic."""
    status = str(row.get("Outreach Status", "")).lower()
    if "already contacted" in status or "sent" in status:
        return False
    return True


def build_blog_prompt(row, template, campaign, matched_artists):
    template_body = template.get("Body Text", "") if template else ""
    template_tone = template.get("Angle/Tone", "") if template else ""

    facts_block = "\n".join(f"- {k}: {v}" for k, v in campaign.items())

    similar_artists_reference = campaign.get("similar_artists_reference", {})
    similar_artist_note = ""
    if matched_artists:
        angle_notes = [similar_artists_reference[a] for a in matched_artists]
        artist_list = ", ".join(matched_artists)
        combined_angles = "; ".join(angle_notes)
        similar_artist_note = (
            f"\nThis outlet has covered artists similar to this campaign before: {artist_list}. "
            f"Angle guidance per artist: {combined_angles}. "
            f"IMPORTANT: if you reference more than one of these artists, do NOT list them "
            f"mechanically one-by-one (e.g. 'similar to X because of A, similar to Y because "
            f"of B'). Instead, synthesize the shared qualities into a single natural sentence "
            f"that names the artists together and blends their angles into one description -- "
            f"for example: 'similar to {artist_list.split(', ')[0]} and {artist_list.split(', ')[-1]} "
            f"for its [combined quality from the angles above]'. Only mention artists whose angle "
            f"actually fits naturally; don't force in every name if it reads awkwardly."
        )

    submission_method = str(row.get("Submission Method", "")).lower()
    if "form" in submission_method or "portal" in submission_method:
        cta_guidance = f"End by directing them to submit via their form/portal: {row.get('Contact Info', '')}"
    else:
        cta_guidance = "End with a natural reply-to-this-email close."

    context_fields = f"""
Blog name: {row.get('Blog/Publication Name', 'Unknown blog')}
Genre covered: {row.get('Genre', '')}
Subgenre covered: {row.get('Subgenre', '')}
Submission method: {row.get('Submission Method', '')}
Notes: {row.get('Status Notes', '')}
"""

    prompt = f"""{ANTI_FABRICATION_RULE}

You are drafting a short outreach pitch. Template tone/angle to follow: {template_tone}
Template structure reference (adapt, don't copy verbatim): {template_body}

Song facts (the only facts you may use):
{facts_block}

Target details:
{context_fields}
{similar_artist_note}

Keep it under 150 words. Do not use "just dropped" or similar brand-new-release
framing -- reference the track naturally instead. {cta_guidance}
Write only the pitch text, no subject line, no preamble, no explanation."""
    return prompt


def build_curator_prompt(row, template, campaign):
    template_body = template.get("Body Text", "") if template else ""
    template_tone = template.get("Angle/Tone", "") if template else ""
    facts_block = "\n".join(f"- {k}: {v}" for k, v in campaign.items())
    target_name = row.get("Playlist Name", row.get("Name", "Unknown curator"))
    context_fields = f"""
Curator/playlist name: {target_name}
Genre/vibe notes: {row.get('Genre/Vibe Notes', row.get('Genre', ''))}
"""
    prompt = f"""{ANTI_FABRICATION_RULE}

You are drafting a short outreach pitch. Template tone/angle to follow: {template_tone}
Template structure reference (adapt, don't copy verbatim): {template_body}

Song facts (the only facts you may use):
{facts_block}

Target details:
{context_fields}

Keep it under 150 words, plain language, playlist-name-forward.
Write only the pitch text, no subject line, no preamble, no explanation."""
    return prompt


def draft_pitch(claude, prompt):
    response = claude.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def append_to_queue(sheet, target_type, target_name, draft_text, notes=""):
    queue_ws = sheet.worksheet("Review Queue")
    today = datetime.date.today().isoformat()
    queue_ws.append_row([today, "email", target_name, draft_text, "pending", notes])


def run(dry_run=True, config_path=None):
    campaign = load_campaign_config(config_path)
    print(f"[config] Loaded campaign: {campaign.get('artist')} - {campaign.get('title')}")

    sheet, claude = get_clients()
    templates = load_templates(sheet)

    blogs = load_rows(sheet, "Blogs")
    curators = load_rows(sheet, "Curators")

    total_drafted = 0

    # --- Blogs pass ---
    blog_template = templates.get("blog")
    if not blog_template:
        print("[warn] No 'blog' template found in Pitch Templates tab -- skipping blogs.")
    else:
        for row in blogs:
            target_name = row.get("Blog/Publication Name", "Unknown")
            check = check_blog_eligibility(row, campaign)

            if not check["eligible"]:
                if dry_run:
                    print(f"[dry-run] SKIP blog '{target_name}': {check['reason']}")
                continue

            note = check["reason"]
            if check["matched_artists"]:
                note += f"; similar artist match: {', '.join(check['matched_artists'])}"

            if dry_run:
                print(f"[dry-run] WOULD DRAFT blog '{target_name}' ({note})")
                continue

            try:
                prompt = build_blog_prompt(row, blog_template, campaign, check["matched_artists"])
                draft_text = draft_pitch(claude, prompt)
                append_to_queue(sheet, "blog", target_name, draft_text, notes=note)
                print(f"[ok] Drafted + queued blog: {target_name}")
                total_drafted += 1
                time.sleep(DRAFT_DELAY_SECONDS)
            except Exception as e:
                print(f"[error] Failed on blog '{target_name}': {e}")

    # --- Curators pass ---
    curator_template = templates.get("curator")
    if not curator_template:
        print("[warn] No 'curator' template found in Pitch Templates tab -- skipping curators.")
    else:
        for row in curators:
            target_name = row.get("Playlist Name", row.get("Name", "Unknown"))
            if not is_curator_eligible(row):
                if dry_run:
                    print(f"[dry-run] SKIP curator '{target_name}': already contacted")
                continue

            if dry_run:
                print(f"[dry-run] WOULD DRAFT curator '{target_name}'")
                continue

            try:
                prompt = build_curator_prompt(row, curator_template, campaign)
                draft_text = draft_pitch(claude, prompt)
                append_to_queue(sheet, "curator", target_name, draft_text)
                print(f"[ok] Drafted + queued curator: {target_name}")
                total_drafted += 1
                time.sleep(DRAFT_DELAY_SECONDS)
            except Exception as e:
                print(f"[error] Failed on curator '{target_name}': {e}")

    print(f"\nDone. {'(dry run -- nothing written)' if dry_run else f'{total_drafted} pitches queued as pending.'}")


if __name__ == "__main__":
    # Optional: pass a specific campaign config file as the first argument,
    # e.g. `python3 draft_pitches.py JSON/song2.json`, to override the
    # CAMPAIGN_CONFIG_PATH variable above for just this run.
    # If omitted, uses whatever CAMPAIGN_CONFIG_PATH is currently set to.
    cli_config_path = sys.argv[1] if len(sys.argv) > 1 else None

    # Flip to False once you've confirmed the dry run output looks right.
    # Dry run does NOT call Claude and does NOT write anything -- it only
    # prints eligibility decisions row by row, so you can sanity-check the
    # genre/subgenre/outreach/contact-status logic before spending any API calls.
    run(dry_run=True, config_path=cli_config_path)