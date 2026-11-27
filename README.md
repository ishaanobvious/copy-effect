# Copy Effect

**Copy the editing style of any video onto your own footage, using Claude Code.**

Give Claude a reference video (a file or an Instagram / TikTok / YouTube link) and your raw clip.
It measures how the reference is edited, shows you a one-page **style card**, renders your footage
in that style, and scores the result against the reference until it matches.

```
> /copy-effect  reference: https://www.instagram.com/reel/XXXX   my footage: raw/take1.mov
```

or just say *"edit my clip like this reel"* and drop both files.

## What it copies

| | |
|---|---|
| **Pacing** | pause removal, jump cuts, shot length, cut rhythm in the hook |
| **Framing** | aspect (9:16, 1:1, 16:9), punch-in pattern (e.g. normal ↔ 1.15× every shot), speed-up |
| **Colour** | brightness, contrast, saturation and tint, matched with a generated LUT |
| **Captions** | word-by-word captions in the reference's look: font, size, case, colour, stroke, shadow, box, active-word highlight, position |
| **Text** | titles, headers and labels on any time range |
| **B-roll** | full-screen cut-aways and inset images/clips |
| **Sound** | voice clean-up, music bed with ducking, sound effects, loudness matched to the reference |

What it does **not** copy automatically (animated zooms, kinetic type, 3D, stickers, transitions
other than hard cuts, tracked cut-outs) is listed on the style card, so you know before rendering.

## How it works

1. `analyze.py` measures the reference: cuts (hard and jump cuts), shot lengths, colour stats and
   palette, loudness, pauses, speech rate, and saves contact sheets plus one frame per shot.
2. Claude looks at the frames for everything numbers can't see (caption style, graphics, framing)
   and writes the style card.
3. Claude maps the card onto a `plan.json`; `build.py --timeline` cuts your footage and prints the
   kept words with output times so captions, titles and B-roll land on the right words.
4. `build.py` renders: cuts → shots with punch-ins → grade → overlays → captions → audio mix →
   loudness.
5. `compare.py` measures your edit the same way and prints a reference-vs-yours table with
   verdicts, plus a side-by-side contact sheet. Claude adjusts and re-renders.

## Install

**1. Get the skill** (personal: all projects, or project: one repo)

```bash
git clone https://github.com/ishaanobvious/copy-effect ~/.claude/skills/copy-effect
```

```bash
git clone https://github.com/ishaanobvious/copy-effect .claude/skills/copy-effect
```

**2. Dependencies**

- [ffmpeg](https://ffmpeg.org) (`brew install ffmpeg` / `sudo apt install ffmpeg` / `winget install ffmpeg`).
  No libass or freetype needed; captions are drawn with Pillow.
- Python 3.9+ and Pillow: `pip install Pillow`
- Optional but recommended: `pip install openai-whisper` (word-timed captions, cuts that never
  clip a word) and `pip install yt-dlp` (analyse references straight from a link).

Or all at once: `pip install -r ~/.claude/skills/copy-effect/requirements.txt`

**3. Use it.** Start Claude Code in the folder with your footage and type `/copy-effect`, or ask in
plain words.

## Run the scripts yourself

```bash
python3 scripts/analyze.py reference.mp4 --out work/ref          # or a URL
python3 scripts/build.py work/plan.json --timeline               # cut + output-time transcript
python3 scripts/build.py work/plan.json --preview                # fast half-size render
python3 scripts/build.py work/plan.json                          # full render
python3 scripts/compare.py work/ref/style_dna.json work/edit.mp4 # score against the reference
```

Every plan key is documented in [`references/plan-format.md`](references/plan-format.md); a full
example is in [`examples/plan.example.json`](examples/plan.example.json).

Real `compare.py` output from a test: a talking head shot in a dark room, copying a reel filmed in a
bright white kitchen. Pacing and sound match. The grade stops short of the reference on purpose,
because matching it fully would wash out the face.

```
                         reference   your edit   verdict
--------------------------------------------------------
aspect                        9:16        9:16   ok
fps                           30.0        30.0   ok
cuts / min                    18.3        17.9   ok
median shot s                 2.77        3.12   ok
cuts in first 3 s                1           0   ok
brightness (0-255)          133.68      108.71   LOW
contrast (luma std)          43.96       55.94   HIGH
saturation                   0.101       0.197   HIGH
warmth                        0.03       0.055   ok
loudness LUFS                -14.3       -14.3   ok
```

## Troubleshooting

- **Whisper model download fails with an SSL error.** Download the model file manually into
  `~/.cache/whisper/` (URLs are in `whisper/__init__.py` of the openai-whisper package). The scripts
  use any model already in that folder if the one you asked for can't load.
- **No captions / cuts on silence instead of words.** Whisper isn't installed; the edit still
  renders, cutting on silence.
- **Washed-out colours from an iPhone clip.** The clip is HDR (HLG). Export it as SDR / "Most
  Compatible" first.
- **A link won't download.** Private or login-walled posts need to be downloaded by you; pass the file.

## Use it responsibly

This copies a *style*, which isn't owned by anyone. It never reuses the reference's footage,
music, voice or logos, and you shouldn't either: use your own footage, music you have the rights
to, and free-licence B-roll. Crediting the creator who inspired the style is a nice touch.

## License

Code: MIT ([LICENSE](LICENSE)). Bundled fonts (Montserrat, Inter, Anton): SIL Open Font License
1.1 ([assets/fonts/OFL.txt](assets/fonts/OFL.txt)).
