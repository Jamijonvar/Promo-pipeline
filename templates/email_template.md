# Blog Pitch Email Template

Reference template for the eventual drafting step in `draft_pitches.py`. Not wired into the code yet — this is the content spec to build the prompt/output around later.

## Subject line

Fixed format, no drafting needed:

```
New music for {blog_name}: {artist} — {title}
```

## Body structure

1. **Greeting** — generic, no invented editor name (we don't reliably have one per row):
   `Hi {blog_name} team,`

2. **Hook / fit line** — one sentence tying the track's genre/subgenre to what this blog covers. If a genuine similar-artist match was found (via `similar_artists_reference`), work it in here — blended naturally if more than one, never a mechanical list.

3. **Core pitch** — 2–3 sentences: artist name, track title, genre/subgenre/mood, one distinguishing creative detail. Only facts present in the campaign JSON — nothing invented.

4. **Link** — the track link, on its own line.

5. **Call to action / sign-off** — plain, low-pressure close. Reply-to-this-email framing (this template is email-only rows; form/portal/social rows are filtered out upstream).

6. **Sign-off**
   ```
   Best,
   {artist}
   ```

## Rules baked in

- Body under ~150 words.
- Never invent streaming numbers, chart positions, press mentions, follower counts, or any momentum claim not explicitly given in the campaign facts.
- No "just dropped" / brand-new-release framing — reference the track naturally instead.
- Similar-artist references only when a real match exists; blend multiple into one natural sentence rather than listing them.
- No hype-speak, no exclamation-point stacking.

## Worked example (illustrative — not a real submission)

Using the current campaign (`json/nicolas-cage.json`) and a hypothetical email-contact blog:

**Subject:**
```
New music for Example Trap Metal Blog: Jonny Wolf — Nicolas Cage
```

**Body:**
```
Hi Example Trap Metal Blog team,

Been following your coverage of the trap-metal/lyrical-hip-hop crossover space and
thought "Nicolas Cage" would be a fit — it sits in that same lineage as Ghostemane
and City Morgue, aggressive and mosh-oriented with a screamed chorus, but the verses
lean into a technical, pocket-heavy flow closer to Tech N9ne or MF DOOM.

Track: "Nicolas Cage" — Jonny Wolf
Genre/mood: Trap Metal / Lyrical Hip Hop, high energy, aggressive, 144–150 BPM, C# minor

Listen: https://open.spotify.com/track/7aH78cimt3uZbviBcpwBTb

Would love to know what you think — happy to send anything else you need.

Best,
Jonny Wolf
```

## Open placeholders for the eventual code

- `{blog_name}` — from the `Blogs` column
- `{artist}`, `{title}`, `{link}` — from the campaign JSON
- Hook sentence — drafted per row (genre/subgenre fit + optional similar-artist blend)
- Core pitch sentence(s) — drafted per row, campaign-facts only
