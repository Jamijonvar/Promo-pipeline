"""
draft_pitches.py

NEW ARCHITECTURE (Aug 2026): this script does matching and mechanical
template-filling ONLY. It never calls the Claude API and never sends
anything. It reads the Publications tab, figures out who's eligible using
deterministic rules, fills in the master email template with their real
data, and writes the whole batch to a single output JSON file that gets
overwritten every run.

The filled emails are intentionally allowed to read a little clunky in
spots the template can't handle gracefully -- most notably when more than
one similar artist matches, the fill produces a raw "X (angle) and Y
(angle)" list rather than natural prose. That's expected. A Claude Cowork
session picks up the output file next, humanizes anything that reads
mechanically (following templates/email_template.md as its style guide),
and handles the actual send after a batch-level go-ahead from Jonny. This
script's job ends the moment the output file is written.

THE LOOP:
  campaign config JSON -> this script -> output JSON (overwritten) -> Cowork -> email

Run this after:
  - Publications tab is populated with real data, named exactly "Publications"
  - templates/email_template.txt exists and has real placeholder content
  - CAMPAIGN_CONFIG_FILENAME below points at the right song's config

Setup:
  pip install gspread google-auth
  Place your Google service account JSON at the path in SHEET_CREDENTIALS_PATH

--- Sep 2026 cleanup notes ---
  - All in-repo paths (campaign config, master template, output file) are
    now anchored to the repo root via REPO_ROOT instead of being relative
    to whatever directory the script happens to be launched from -- it was
    silently breaking (FileNotFoundError) if run from anywhere but the repo
    root.
  - TEMPLATE_PATH now points at templates/email_template.txt (this file
    didn't exist before -- the script was pointed at a plain "email_template.txt"
    that was never created, so every run failed immediately). See that file
    for the actual {{TOKEN}} template consumed by fill_template() below.
    templates/email_template.md is a *separate* file: a style guide for the
    Cowork humanize pass, not something this script reads.
  - OUTPUT_PATH now writes to outputs/ (plural) to match .gitignore, which
    already excludes "outputs/" -- it was writing to "output/" (singular),
    so filled pitch batches (real contact info + copy) were NOT actually
    gitignored.
  - Fixed a real eligibility bug: the "already contacted" check used to do
    `"sent" in outreach_status`, which is a substring match -- so a status
    of "Not Sent" or "Unsent" (both meaning NOT yet contacted) contained
    "sent" and was wrongly treated as already contacted, silently skipping
    otherwise-eligible blogs. See _status_means_already_contacted() below.

--- Sep 2026 live-validation notes (run against the real sheet) ---
  - The tab is actually named "Publications", not "Blogs" -- there is no
    "Blogs" tab in the sheet at all. Fixed.
  - The publication-name COLUMN in that tab is header "Blogs" (confusingly
    -- that's presumably where the old "Blogs"-as-tab-name assumption came
    from). The code was looking for a "Blog/Publication Name" column that
    doesn't exist, so target_name and {{BLOG_NAME}} were always falling
    back to "Unknown"/"there". Fixed.
  - Every one of the 419 real rows currently has the exact same
    boilerplate Outreach Status text: "Not yet contacted. If emailed
    before for this song, ignore and move to next row." That string
    contains "contacted" as a substring, so _status_means_already_contacted
    (the fix above) was STILL wrongly flagging every single row as
    already-contacted against real data -- a live run would have produced
    zero eligible pitches. Added "not yet" as a not-contacted override,
    checked before the "contacted"/"sent" markers, to fix this. This is
    still just pattern-matching against today's one known boilerplate
    value -- there's no established convention yet for what a row looks
    like once it actually HAS been contacted (nothing in the sheet has
    ever been marked sent). Worth nailing down with Jonny before the first
    real send, so this function can be kept in sync with whatever
    convention he actually uses.
"""

import os
import sys
import json
import pathlib
import datetime

import gspread
from google.oauth2.service_account import Credentials

# --- Configuration ---------------------------------------------------------

SHEET_ID = "1zQTh1KvorgbCykJ5NhxBdRaiUE987qjlXK8q1VvyamA"

SHEET_CREDENTIALS_PATH = os.path.expanduser(
    "~/Documents/promo pipeline (non git)/promo-pipeline-504015-9a37b1dc173a.json"
)
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Repo root, regardless of the directory this script is launched from.
# (This file lives at <repo root>/Py scripts/draft_pitches.py.)
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

# *** THE ONE LINE TO CHANGE FOR EACH NEW RELEASE ***
CAMPAIGN_CONFIG_FILENAME = "nicolas-cage.json"
CAMPAIGN_CONFIG_PATH = REPO_ROOT / "json" / CAMPAIGN_CONFIG_FILENAME

# The master email template -- one file, reused across every campaign.
# Plain {{TOKEN}} / {{#IF_X}}...{{/IF_X}} mechanical fill -- see
# fill_template() below for the exact tokens it understands.
TEMPLATE_PATH = REPO_ROOT / "templates" / "email_template.txt"

# Fixed output path -- overwritten every run, not per-campaign.
# "outputs/" (plural) matches the .gitignore entry -- real contact info
# and pitch copy should never end up in commit history.
OUTPUT_PATH = REPO_ROOT / "outputs" / "pending_emails.json"

REQUIRED_CONFIG_KEYS = ["title", "artist", "genre", "subgenre", "link"]


def load_campaign_config(path=None):
    if path is None:
        path = CAMPAIGN_CONFIG_PATH

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No campaign config found at '{path}'. This should be a stable, "
            f"already-created file -- it's never generated by this script."
        )

    with open(path, "r") as f:
        config = json.load(f)

    def _has_placeholder(value):
        # "genre"/"subgenre" may be a list (see _as_list()) -- catch a
        # stray "REPLACE_ME" left inside a list too, not just a bare
        # string value.
        if isinstance(value, (list, tuple)):
            return any(v == "REPLACE_ME" for v in value)
        return value == "REPLACE_ME"

    missing = [k for k in REQUIRED_CONFIG_KEYS if not config.get(k) or _has_placeholder(config.get(k))]
    if missing:
        raise ValueError(
            f"Campaign config at '{path}' is missing or has placeholder "
            f"values for: {', '.join(missing)}."
        )

    return config


def get_sheet():
    creds = Credentials.from_service_account_file(SHEET_CREDENTIALS_PATH, scopes=SCOPES)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def load_rows(sheet, tab_name):
    try:
        ws = sheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        print(f"[skip] Tab '{tab_name}' not found -- skipping this source.")
        return []
    return ws.get_all_records()


def _contains(haystack, needle):
    """Case-insensitive substring check, tolerant of empty/missing cells."""
    if not haystack or not needle:
        return False
    return needle.strip().lower() in str(haystack).strip().lower()


def _as_list(value):
    """
    Normalize a campaign config value that may be either a single string
    or a list of strings into a list of strings. Lets "genre" and
    "subgenre" in the campaign JSON hold multiple possible tags --
    e.g. "subgenre": ["Lyrical Hip Hop", "Trap Metal", "Hip Hop"] -- while
    staying backward-compatible with older configs that just use a single
    string. Blank/empty entries are dropped.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    value = str(value).strip()
    return [value] if value else []


def _any_contains(haystack, needles):
    """True if haystack contains ANY of the given needle strings."""
    return any(_contains(haystack, needle) for needle in needles)


# Statuses that mean "we have NOT contacted them yet" but that would
# otherwise false-positive against a naive "sent"/"contacted" substring
# check (e.g. "Not Sent" and "Unsent" both contain "sent"; the sheet's
# real boilerplate "Not yet contacted..." contains "contacted"). Checked
# before the already-contacted markers below, so these always win.
_NOT_CONTACTED_OVERRIDES = ("not sent", "unsent", "not contacted", "not yet", "no contact")

# Substrings that mean "we HAVE already contacted them", once the
# not-contacted overrides above have been ruled out.
_ALREADY_CONTACTED_MARKERS = ("already contacted", "contacted", "sent")


def _status_means_already_contacted(status_text):
    """
    True if this Outreach Status cell indicates we've already reached out
    (e.g. "Sent", "Already Contacted", "Sent 8/12"). False for anything
    that means the opposite even though it contains "sent" as a substring
    -- e.g. "Not Sent", "Unsent", "Not Sent Yet".
    """
    status_text = str(status_text or "").strip().lower()
    if not status_text:
        return False
    if any(marker in status_text for marker in _NOT_CONTACTED_OVERRIDES):
        return False
    return any(marker in status_text for marker in _ALREADY_CONTACTED_MARKERS)


def check_blog_eligibility(row, campaign):
    """
    Matching pipeline for a single Publications-tab row.
    Returns {eligible, reason, matched_artists}.

    Sep 2026: broadened from the original version, which only checked
    campaign genre against the row's Genre column, falling back to
    campaign subgenre against the row's Subgenre column ONLY if genre
    didn't already hit. Against the real sheet, specific tags like "Trap
    Metal" often live inside the Subgenre column as one of several
    semicolon-separated values (e.g. "Nu Metal; Trap Metal crossover;
    Hard Rock"), while the Genre column holds broad umbrella categories
    (Alternative, Metal, Hip Hop...) -- so a campaign's genre/subgenre
    terms can legitimately show up in either sheet column. Now checks
    both campaign terms (genre, subgenre) against both sheet fields
    (Genre, Subgenre) -- any hit makes the row eligible.

    "genre" and "subgenre" in the campaign JSON can each be a single
    string OR a list of strings (see _as_list()) -- e.g.
    "subgenre": ["Lyrical Hip Hop", "Trap Metal", "Hip Hop"] to match on
    any of several possible tags. A row is eligible if ANY campaign
    genre/subgenre term is found in ANY sheet Genre/Subgenre field.
    """
    genre_field = row.get("Genre", "")
    subgenre_field = row.get("Subgenre", "")
    outreach_status = row.get("Outreach Status", "")
    contact_status = str(row.get("Contact Status", "")).lower()
    notable_artists = row.get("Notable Artists Covered", "")

    similar_artists_reference = campaign.get("similar_artists_reference", {})
    campaign_terms = _as_list(campaign["genre"]) + _as_list(campaign["subgenre"])

    genre_field_hit = _any_contains(genre_field, campaign_terms)
    subgenre_field_hit = _any_contains(subgenre_field, campaign_terms)

    if not genre_field_hit and not subgenre_field_hit:
        return {"eligible": False, "reason": "no genre/subgenre match", "matched_artists": []}

    if _status_means_already_contacted(outreach_status):
        return {"eligible": False, "reason": "already contacted", "matched_artists": []}

    if "defunct" in contact_status:
        return {"eligible": False, "reason": "contact defunct", "matched_artists": []}

    matched_artists = [
        name for name in similar_artists_reference
        if _contains(notable_artists, name)
    ]

    matched_via = "genre column" if genre_field_hit else "subgenre column"
    return {"eligible": True, "reason": f"matched via {matched_via}", "matched_artists": matched_artists}


def load_master_template():
    if not os.path.exists(TEMPLATE_PATH):
        raise FileNotFoundError(
            f"No master template found at '{TEMPLATE_PATH}'. This is the one "
            f"file with {{{{PLACEHOLDER}}}} tokens that every pitch gets built from."
        )
    with open(TEMPLATE_PATH, "r") as f:
        return f.read()


def _fill_conditional_blocks(template, flags):
    """
    Handles {{#IF_X}}...{{/IF_X}} blocks. If flags[X] is True, the tags are
    stripped and the inner content stays. If False, the whole block (tags
    and content) is removed. This is deliberately simple -- not a real
    templating engine, just enough for one conditional (similar artists).
    """
    import re
    for flag_name, flag_value in flags.items():
        pattern = re.compile(
            r"\{\{#IF_" + flag_name + r"\}\}(.*?)\{\{/IF_" + flag_name + r"\}\}",
            re.DOTALL,
        )
        if flag_value:
            template = pattern.sub(r"\1", template)
        else:
            template = pattern.sub("", template)
    return template


def fill_template(template, row, campaign, matched_artists):
    """
    Purely mechanical fill -- no judgment, no rewriting. When multiple
    similar artists match, this deliberately produces a raw, list-like
    sentence rather than trying to write natural prose -- that's Cowork's
    (or Claude Code's) job, not this function's.

    Two similar-artist cases, mutually exclusive:
    - VERIFIED: this specific outlet's "Notable Artists Covered" cell
      actually mentioned one of our reference artists -> confident framing
      ("shares qualities with X, who they've covered before").
    - FALLBACK: no outlet-specific match, but we still want a genre-level
      comparison point -> softer framing ("in the vein of X and Y"),
      using up to the first 2 names from the reference list. This is a
      deterministic rule (always the same 2 names, same order), not a
      judgment call, so it lives here in Python rather than being left to
      Claude at cleanup time.
    """
    similar_artists_reference = campaign.get("similar_artists_reference", {})

    # "genre" and "subgenre" may each be a single string or a list of
    # strings (see _as_list()) -- always join to plain text here so the
    # filled email never shows a raw Python list.
    genre_display = "/".join(_as_list(campaign.get("genre")))
    subgenre_list = _as_list(campaign.get("subgenre"))
    subgenre_suffix = f" with a {'/'.join(subgenre_list)} edge" if subgenre_list else ""

    similar_artists_list = ""
    fallback_artists_list = ""

    if matched_artists:
        parts = [f"{name} ({similar_artists_reference[name]})" for name in matched_artists]
        if len(parts) == 1:
            similar_artists_list = parts[0]
        else:
            similar_artists_list = ", ".join(parts[:-1]) + " and " + parts[-1]
    else:
        fallback_names = list(similar_artists_reference.keys())[:2]
        if fallback_names:
            fallback_artists_list = " and ".join(fallback_names)

    submission_method = str(row.get("Submission Method", "")).lower()
    contact_info = row.get("Contact Info", "")
    if "form" in submission_method or "portal" in submission_method:
        submission_cta = f"Please submit via their form/portal: {contact_info}"
    else:
        submission_cta = "Feel free to reply directly to this email."

    filled = _fill_conditional_blocks(template, {
        "SIMILAR_VERIFIED": bool(matched_artists),
        "SIMILAR_FALLBACK": bool(not matched_artists and fallback_artists_list),
    })

    replacements = {
        "{{TITLE}}": campaign.get("title", ""),
        "{{ARTIST}}": campaign.get("artist", ""),
        "{{BLOG_NAME}}": row.get("Blogs", "there"),
        "{{GENRE}}": genre_display,
        "{{SUBGENRE_SUFFIX}}": subgenre_suffix,
        "{{MOOD}}": campaign.get("mood", ""),
        "{{SONG_LINK}}": campaign.get("link", ""),
        "{{SIMILAR_ARTISTS_LIST}}": similar_artists_list,
        "{{FALLBACK_ARTISTS_LIST}}": fallback_artists_list,
        "{{SUBMISSION_CTA}}": submission_cta,
    }
    for token, value in replacements.items():
        filled = filled.replace(token, str(value))

    return filled


def run(config_path=None):
    campaign = load_campaign_config(config_path)
    print(f"[config] Loaded campaign: {campaign.get('artist')} - {campaign.get('title')}")

    template = load_master_template()
    sheet = get_sheet()
    blogs = load_rows(sheet, "Publications")

    results = []
    skipped_count = 0

    for row in blogs:
        target_name = row.get("Blogs", "Unknown")
        check = check_blog_eligibility(row, campaign)

        if not check["eligible"]:
            skipped_count += 1
            continue

        filled_email = fill_template(template, row, campaign, check["matched_artists"])

        results.append({
            "target_name": target_name,
            "target_type": "blog",
            "matched_via": check["reason"],
            "matched_artists": check["matched_artists"],
            "genre": row.get("Genre", ""),
            "subgenre": row.get("Subgenre", ""),
            "submission_method": row.get("Submission Method", ""),
            "contact_info": row.get("Contact Info", ""),
            "source_url": row.get("Source URL", ""),
            "status_notes": row.get("Status Notes", ""),
            "filled_email": filled_email,
        })

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "generated_at": datetime.datetime.now().isoformat(),
        "campaign": {
            "title": campaign.get("title"),
            "artist": campaign.get("artist"),
            "link": campaign.get("link"),
        },
        "pending_emails": results,
    }
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"[done] {len(results)} emails filled, {skipped_count} rows skipped.")
    print(f"[done] Written to {OUTPUT_PATH} -- ready for Cowork to review and send.")


if __name__ == "__main__":
    cli_config_path = sys.argv[1] if len(sys.argv) > 1 else None
    run(config_path=cli_config_path)
