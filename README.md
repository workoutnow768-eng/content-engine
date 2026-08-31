# Content Engine — Volt Ads Studio

The production machine behind the "Content, sold monthly" service on
voltadsstudio.com. One renderer, many **niche packs**. A client picks a plan
and a preset niche (or asks for a custom one), we point the engine at their
brand config, and it produces daily 4-slide carousel posts ready for Buffer.

Sibling of `recipe-page/` and `workout-page/` — same persistence pattern
(per-client `state.json`, rotation index, `scheduled_up_to`), but generalized:
content and styling live in data packs, not in the code.

## Layout

```
content-engine/
  engine/render.py      the renderer (niche-agnostic, PIL)
  niches/*.json         preset niche packs (content bank + visual style)
  clients/<name>/
    config.json         brand handle, colors override, plan, platforms
    state.json          rotation index, scheduled_up_to, status
  out/<client>/<entry>/slide1..4.jpg
```

## Preset niches (v1)

| pack | vibe | bank size |
|---|---|---|
| creepy_history | dark, blood-red accent, unsettling facts | 10 entries |
| tech_facts | deep navy, electric cyan | 10 entries |
| fitness_tips | volt yellow on black, punchy | 10 entries |
| money_facts | deep green, gold accent | 10 entries |

Each entry = hook slide + 3 fact slides + caption + hashtags. Growing a pack
is pure data entry in the JSON — no code changes. Target: 30+ entries per
pack so a monthly client never sees a repeat inside one plan cycle.

## Render

```
python engine/render.py --niche creepy_history --entry 0 --client EXAMPLE
python engine/render.py --niche tech_facts --all --client EXAMPLE   # whole bank
```

Fonts: put Montserrat-ExtraBold.ttf / Montserrat-Medium.ttf in engine/fonts/
(falls back to DejaVu automatically if missing).

## Client workflow (per order)

1. Copy `clients/EXAMPLE` to `clients/<brand>`, set handle, plan, platforms,
   optional color override (their brand color replaces the niche accent).
2. Render the month: entries `state.last_index+1 ...` per plan post count
   (Spark 15 / Current 30 / Grid 60, two a day).
3. Eyeball every slide (non-negotiable, same rule as recipe-page).
4. Schedule via Buffer — same flow as recipe-page: verify the channel sidebar
   before scheduling, respect Instagram's hashtag cap via
   "Customize for each network".
5. Update the client's `state.json`: `last_index`, `scheduled_up_to`.

## Quality bar (why clients stay)

- Hook slide must earn the swipe: shortest possible scary/curious claim.
- One fact per slide, max ~24 words body.
- Facts must be TRUE and verifiable — never invent. Check anything dubious.
- Brand handle footer on every slide (their handle, not ours).
- Consistent visual identity inside a pack; client color override only
  recolors the accent, never breaks the layout.
