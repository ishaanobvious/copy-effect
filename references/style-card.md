# Style card template

Write `style_card.md` in this shape. Each line is a measured number (cite the report) or something
seen in a named frame (cite `shots/shot_007_…jpg`). Keep it to one screen.

```markdown
# Style card — <reference name>

**In one line:** <e.g. "calm 9:16 talking head, locked-off framing, jump cut every ~2.8 s,
small lowercase captions under a persistent two-line title, soft music bed, bright desaturated look">

## Format
- 1080x1920 9:16, 30 fps, 39 s

## Pacing
- 18 cuts/min, median shot 2.8 s (p10 1.9, p90 4.6); 1 cut in the first 3 s
- Pauses: none left over 0.18 s → tight cut

## Framing
- Medium shot, head at ~30% from top, no punch-ins (shots 1–13 identical framing)

## Captions & text
- Title "N Rules to …" two lines, white, medium weight, y≈0.21, whole video (shot_001)
- Captions: lowercase sentence fragments, white, small (~4% of width per letter), soft shadow,
  no highlight, y≈0.33, two lines max (shot_004)

## Graphics & B-roll
- None until the end card (shot_013: big quoted keyword over the chest)

## Colour
- Bright (luma 134), low contrast, very low saturation (0.10), neutral

## Sound
- -14 LUFS, music bed likely, no sfx heard/seen

## Hook & ending
- Opens mid-sentence on rule 1, no intro; ends on a "comment LOVE" style keyword card

## Copied by the renderer
- cuts + pacing, framing/zoom pattern, grade, captions, title, music bed + ducking, loudness

## Needs manual work / not copied
- <anything from the table below, with a suggestion>
```

## What the renderer can and can't copy

| Copied by `build.py` | Not copied automatically (say so; offer a route) |
|---|---|
| Pause cutting, jump cuts, shot length, punch-in pattern | Animated zooms (slow push-ins, whip zooms, shakes) — static zoom per shot only |
| Aspect change (crop / blurred fill / bars), speed-up | Face/subject tracking crops — anchor is fixed per shot |
| Colour grade toward the reference (LUT) or your own `.cube` | Film grain, light leaks, glows, VHS — add a filter or a pre-made overlay video |
| Word-by-word captions: font, size, case, colour, stroke, shadow, box, word highlight (colour or pill), position | Animated caption pop/bounce per word, emoji in captions, captions behind the subject |
| Static titles, labels, headers on a time range | Kinetic typography, animated graphics, progress bars, 3D, stickers, arrows that draw on |
| Full-screen and inset B-roll / images | Masked or tracked cut-outs, split-screen layouts, picture-in-picture with borders/corners |
| Music bed with ducking, sfx at times, loudness match | Transitions other than hard cuts (dissolves, whips, glitches) |

For anything in the right column: describe it in the style card, then either build it with a
motion-graphics tool the user has (any HTML-to-video or After Effects workflow) as an extra
overlay rendered to `.mov`/`.png` and added under `overlays`, or leave it out and say so.
