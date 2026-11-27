# From reference DNA to plan settings

| Measured in `style_dna.json` / seen in frames | Plan setting |
|---|---|
| `format.width/height/fps` | `format` (match the reference's aspect; 1080 wide is enough) |
| Reference aspect ≠ footage aspect | `format.fit`: `crop` for a talking head (keep the face), `blur` for screen recordings or wide shots you can't crop |
| `pacing.shot_len_median` | `shots.target_len` |
| Jump cuts only, framing never changes | `shots.zooms: [1.0]`; still cut pauses |
| Framing alternates normal / tighter | `shots.zooms: [1.0, 1.12]` (subtle) to `[1.0, 1.25]` (aggressive); three-step `[1.0, 1.15, 1.3]` for hype edits |
| `pacing.cuts_in_first_3s` ≥ 2 | Fast hook: lower `target_len` for the first sentence with `shots.list`, or start on the strongest line via `cut.remove` |
| `audio.pauses_over_0_18s` ≈ 0 | Tight edit: `cut.min_pause: 0.2–0.25`, `keep_pause: 0.05–0.06` |
| Breathing room between lines | `cut.min_pause: 0.45`, `keep_pause: 0.12` |
| `speech.words_per_minute` well above the user's | `speed: 1.05–1.15` (pitch is kept); don't go past 1.2 |
| `color.luma_mean`, `luma_std`, `saturation_mean`, `warmth` | `grade.match_reference: true`, `strength 0.5–0.8` |
| Reference is mostly graphics/B-roll, not a person | lower `grade.strength` (0.3) — its colour stats describe the graphics, not a skin tone |
| `audio.integrated_lufs` | used automatically as the loudness target |
| `audio.music_bed` likely / you hear music in a shot | `audio.music` + `music_db -20…-26`; ask the user for a track |
| Captions seen in frames | `captions.*` — font feel → bundled font (bold geometric → Montserrat, neutral clean → Inter, tall condensed → Anton), case, size ≈ cap height relative to width (a word 1/4 of the frame wide at 72 px is typical), colour, stroke/shadow/box, `y`, `words_per_line`, highlight |
| Persistent title/header | `texts` with `start: 0`, `end`: full length |
| Number/label pops on the beat | `texts` short items at the word's output time + an `sfx` click |
| B-roll cut-aways | `overlays` `mode: full`, 1–2.5 s each, on the noun being said |
| Inset photos/cards | `overlays` with `w 0.6–0.85`, `y` above the speaker's head |

## Closing the gap (compare.py rows)

| Row says | Change |
|---|---|
| cuts / min LOW | lower `shots.target_len`; add a third zoom step; lower `cut.min_pause` |
| cuts / min HIGH | raise `shots.target_len` |
| brightness LOW / HIGH | raise `grade.strength`; or `grade.eq.brightness` ±0.03–0.08 |
| contrast LOW / HIGH | `grade.eq.contrast` 0.9–1.2 (eq replaces the LUT) or raise `strength` |
| saturation LOW / HIGH | raise `strength`; or `grade.eq.saturation` 0.7–1.3 |
| warmth off | the LUT moves tint at `strength`; for more, a custom `.cube` |
| loudness off | set `audio.lufs` explicitly |
| cuts in first 3 s LOW | split the first sentence into short shots with `shots.list` |

Colour rows can stay off on purpose: if matching would blow out skin or the footage was shot in a
very different room, stop at a pleasing middle and say so.
