---
name: copy-effect
description: Copy the editing style of any reference video onto your own footage. Give it a reference (a file or an Instagram / TikTok / YouTube link) plus your raw clip(s). It measures the reference's pacing, cut rhythm, punch-ins, colour, loudness and music and looks at its captions, titles and graphics. It writes a style card you approve, then renders your footage in that style (jump cuts, punch-ins, colour grade matched to the reference, captions in the same look, title text, B-roll overlays, music bed with ducking, sfx, matched loudness) and scores the result against the reference. Use when the user says "copy this edit", "edit it like this video", "make mine look like this reel", "copy effect", "/copy-effect", "steal this editing style", "same style as <link>", or drops a reference video next to their own footage, even if they don't name this skill.
---

# Copy Effect

You copy **how a video is edited**, never **what is in it**. The user gives you a reference video
and their own footage. You measure the reference, describe its style as a short style card, map
that card onto a `plan.json`, render with `scripts/build.py`, and check the result against the
reference with `scripts/compare.py` until it matches.

Never reuse the reference's footage, music, voice, logos or exact on-screen text in the output.
Copy the rhythm, framing, type style, colour and sound design; fill them with the user's content.

Scripts live in this skill folder (`scripts/`); call them with `python3 <skill>/scripts/<name>.py`.
Requirements: `ffmpeg`, Python 3.9+, `Pillow`. Optional: `openai-whisper` (captions and word-accurate
cuts), `yt-dlp` (reference links). If something is missing, say what and how to install it
(`README.md` → Install).

## Workflow

1. **Collect the inputs.** You need (a) the reference: a file path or a link, and (b) the user's raw
   footage: one clip or several in order. Ask only for what is missing. Pick a working folder next
   to the user's footage, for example `copy-effect/<short-name>/`, and keep everything for this edit
   in it: `ref/` (analysis), `plan.json`, the render and its `_work/` folder.

2. **Measure the reference.**
   ```bash
   python3 <skill>/scripts/analyze.py "<reference file or URL>" --out <workdir>/ref
   ```
   It writes `report.md`, `style_dna.json`, `contact_sheet.jpg`, `hook_sheet.jpg`, `shots/` and
   (with whisper) `transcript.json`. If a link fails to download (private, login-walled, rate-limited),
   ask the user to download the video and pass the file.

3. **Look at it.** Read `report.md`, then open `contact_sheet.jpg`, `hook_sheet.jpg` and a spread of
   `shots/*.jpg` (all of them if there are under ~20). Numbers can't see type, graphics or framing,
   so you have to. Note for each: caption font feel, weight, case, size relative to frame width,
   colour, stroke/shadow/box, position (y as a fraction of height), words per line, word highlight;
   title or header text; how tight the speaker is framed and whether shots alternate zoom; B-roll
   (full-screen or inset, how long, how often); text cards, stickers, arrows, progress bars,
   transitions (hard cut, whip, flash, zoom); the hook (first 3 s) and the ending/CTA.
   "Jump" cuts in the report can also be caption or graphic swaps; confirm in the shot frames.

4. **Write the style card** to `<workdir>/style_card.md` using `references/style-card.md`. Every line
   is either a measured number from the report or something you saw in a named frame. Split it into
   *Copied by the renderer* and *Needs manual work / not copied* (see the capability table there), so
   the user knows up front what will and won't match. Show the user the card in a few lines and ask
   one question only if a choice changes the edit (e.g. "the reference has a music bed — do you have
   a track to use, or skip music?"). Otherwise continue.

5. **Check the footage.** `ffprobe` each clip (resolution, aspect, duration, HDR). Then write a first
   `plan.json` (format: `references/plan-format.md`; mapping guide: `references/dna-to-plan.md`) with
   `source`, `reference`, `output`, `format` and `cut`, and run:
   ```bash
   python3 <skill>/scripts/build.py <workdir>/plan.json --timeline
   ```
   It transcribes, cuts pauses and prints the kept words on the **output** timeline. All `texts`,
   `overlays` and `sfx` times in the plan are output seconds, so place them from this list. Add
   `cut.remove` ranges (raw seconds) for false starts and repeated takes, keeping the best read.

6. **Fill in the style** from the card: `shots` (target length = reference median shot, zoom
   pattern), `speed`, `grade`, `captions`, `texts`, `overlays` (only the user's own or free-licence
   assets; ask before downloading stock), `audio` (music only from a track the user owns or a
   royalty-free one they approve), `sfx`.

7. **Preview, look, fix.**
   ```bash
   python3 <skill>/scripts/build.py <workdir>/plan.json --preview
   ```
   Open `<output>_work/check_sheet.jpg`. Check that captions and titles don't cover the face or
   mouth, that text sits inside the platform's safe area (for 9:16 keep important text between
   y 0.14 and 0.65 and 6% in from the sides unless the reference does otherwise), that punch-ins
   don't crop the head, and that overlays land on the right words. Fix the plan and preview again.

8. **Render and score.**
   ```bash
   python3 <skill>/scripts/build.py <workdir>/plan.json
   python3 <skill>/scripts/compare.py <workdir>/ref/style_dna.json <output.mp4>
   ```
   Read the table and `compare.jpg`. For each row marked HIGH / LOW, adjust the plan (see "Closing
   the gap" in `references/dna-to-plan.md`) and render again. Two or three rounds is normal. Stop
   when the rows that matter for this style are `ok`, or explain why one can't be (e.g. the
   reference is brighter because it was shot in a white kitchen; matching it fully would blow out
   the user's face).

9. **Report.** Output path and duration, a 5-line summary of the style you copied, the compare
   table, and the honest list of what wasn't copied (custom animations, 3D, stickers, AI effects)
   with how you'd do each if they want it. If the project has its own review step (for example a
   review or notes skill), run it now.

## Rules

- **Style, not content.** No footage, audio, voice, logos or verbatim text from the reference.
- **Never cut inside a word.** Cuts land in pauses; `cut.mode: "words"` (default when whisper is
  installed) cuts only between words. If a sentence is clipped, widen `keep_pause`.
- **Speaker stays readable.** Never cover the eyes or mouth with captions or overlays; never punch
  in so far that the head is cropped. Prefer moving text over shrinking the speaker.
- **Ask before** downloading stock assets or music, and before using anything that costs money or
  credits.
- **Be honest about the gap.** If the reference uses something the renderer can't do, say so in the
  style card and the report instead of quietly leaving it out.

## Files

- `scripts/analyze.py` measure a reference → `style_dna.json`, `report.md`, sheets, shot frames
- `scripts/build.py` render `plan.json` (`--timeline`, `--preview`)
- `scripts/compare.py` score an edit against the reference DNA
- `references/style-card.md` the style card template + what the renderer can and can't copy
- `references/plan-format.md` every plan key with defaults
- `references/dna-to-plan.md` how each measured number turns into a plan setting
- `examples/plan.example.json` a complete plan (talking-head reel, word-highlight captions)
- `assets/fonts/` Montserrat, Inter, Anton (SIL OFL)
