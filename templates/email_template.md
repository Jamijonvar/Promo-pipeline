# Blog Pitch Email — Cowork Humanize Style Guide

**Role, as of the Sep 2026 cleanup:** `draft_pitches.py` mechanically fills
`templates/email_template.txt` for every eligible row using plain
`{{TOKEN}}` substitution — genre, mood, link, submission CTA, subject
line, sign-off, etc. are all already correct and shouldn't be touched.
The one spot the template can't handle gracefully is the similar-artist
line: when more than one reference artist matches, the fill produces a
raw "X (angle), Y (angle) and Z (angle)" list. **This doc is the style
guide Cowork follows when it reviews a batch from `outputs/pending_emails.json`
and smooths that spot (and anything else that reads mechanically) into
natural prose — it is not a spec for drafting each email from scratch.**
Everything outside the similar-artist sentence should be left as the
script wrote it unless something is actually wrong.

## Subject line

Already generated correctly by the script — leave it alone:

```
New music for {blog_name}: {artist} — {title}
```

## Body structure (as filled by the script)

1. **Greeting** — generic, no invented editor name: `Hi {blog_name} team,`
2. **Hook / fit line** — already filled. This is the one line to reshape:
   turn the raw "X (angle), Y (angle) and Z (angle)" list into one natural
   sentence that blends the angles instead of listing them. Never drop a
   real match, never add an artist that wasn't in the matched list.
3. **Core pitch** — already filled from campaign facts (genre/subgenre/mood).
   Leave as-is unless it reads broken.
4. **Link** — the track link, on its own line. Never touch.
5. **Call to action / sign-off** — already filled based on submission
   method (reply directly vs. form/portal link). Leave as-is.

## Rules for the humanize pass

- Only reshape the similar-artist sentence (and fix anything that's
  actually broken — leftover `{{TOKENS}}`, doubled punctuation, etc.).
  Don't rewrite sentences that already read fine.
- Never invent streaming numbers, chart positions, press mentions,
  follower counts, or any momentum claim not explicitly in the campaign
  facts or the matched-artist data.
- Never introduce a similar-artist name that wasn't in `matched_artists`
  for that row.
- No hype-speak, no exclamation-point stacking, no "just dropped" framing.
- Keep the body under ~150 words after your edits.

## Worked example (real output from the script, Nicolas Cage campaign)

**Before (mechanical fill, 3 similar-artist matches — the expected rough spot):**

> "Nicolas Cage" shares real ground with Ghostemane (lean into the
> trap-metal lineage angle. Mention the screamed chorus), City Morgue
> (lean into the aggressive, mosh-oriented trap-metal angle. Mention the
> screamed chorus) and Suicide Boys (lean into the gritty underground/aggressive
> hip-hop angle), so I thought it might be a fit for what you cover.

**After (humanized by Cowork — same facts, blended into prose):**

> "Nicolas Cage" sits right in your lane — it's got the mosh-oriented,
> screamed-chorus energy of Ghostemane and City Morgue, with verses that
> lean into the gritty, technical flow of Suicide Boys.

Everything else in that email (subject, core pitch, link, CTA, sign-off)
stays exactly as the script filled it.

## Token reference (for maintaining `templates/email_template.txt`)

| Token | Filled from |
|---|---|
| `{{TITLE}}` / `{{ARTIST}}` / `{{GENRE}}` / `{{SUBGENRE_SUFFIX}}` / `{{MOOD}}` / `{{SONG_LINK}}` | campaign config JSON |
| `{{BLOG_NAME}}` | the row's "Blog/Publication Name" |
| `{{SIMILAR_ARTISTS_LIST}}` | verified match(es) — used inside `{{#IF_SIMILAR_VERIFIED}}...{{/IF_SIMILAR_VERIFIED}}` |
| `{{FALLBACK_ARTISTS_LIST}}` | up to 2 reference artists, used when there's no outlet-specific match, inside `{{#IF_SIMILAR_FALLBACK}}...{{/IF_SIMILAR_FALLBACK}}` |
| `{{SUBMISSION_CTA}}` | derived from "Submission Method" (form/portal link vs. reply-direct) |
