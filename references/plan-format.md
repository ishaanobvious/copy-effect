# plan.json format

One file describes the whole edit. Paths are relative to the plan file. Every key except `source`
is optional; defaults are shown. **Times in `texts`, `overlays` and `sfx` are output seconds**
(after cutting and speed). Times in `cut` are raw seconds of the (joined) source. Run
`build.py plan.json --timeline` to see every kept word with its output time.

```jsonc
{
  "source": "raw/take1.mov",            // or ["raw/a.mov", "raw/b.mov"] — joined in order
  "reference": "ref/style_dna.json",     // from analyze.py; used for grade + loudness targets
  "output": "out/edit.mp4",
  "work": "out/edit_work",               // default: <output stem>_work next to the plan
  "whisper_model": "base",               // tiny | base | small | medium; falls back to any cached model

  "format": {
    "width": 1080, "height": 1920, "fps": 30,
    "fit": "crop",                       // crop (fill, cut the sides) | blur (fit over a blurred copy) | pad (black bars)
    "anchor": [0.5, 0.4]                 // where crops and punch-ins centre: x, y as fractions (0.4 keeps heads in frame)
  },

  "cut": {
    "mode": "auto",                      // auto (words if whisper else silence) | words | silence | none | manual
    "min_pause": 0.3,                    // pauses longer than this are removed
    "keep_pause": 0.08,                  // breath kept on each side of a cut
    "noise_db": -35,                     // silence threshold (silence mode)
    "start": null, "end": null,          // trim the source first (raw seconds)
    "remove": [[12.4, 15.1]],            // raw ranges to drop: false starts, repeated takes
    "segments": []                       // manual mode: [[in, out], ...] raw ranges to keep, in order
  },

  "shots": {
    "target_len": 2.5,                   // seconds per shot; long segments split here (at word gaps)
    "zooms": [1.0, 1.12],                // cycled shot by shot: punch-in pattern. [1.0] = no punch-ins
    "anchor": null,                      // override format.anchor for punch-ins
    "list": []                           // full manual control: [{"in": 1.2, "out": 3.4, "zoom": 1.15, "anchor": [0.5, 0.35]}]
  },

  "speed": 1.0,                          // 1.1 = 10% faster, pitch kept

  "grade": {
    "match_reference": true,             // build a LUT that moves brightness, contrast, saturation and tint toward the reference
    "strength": 0.6,                     // 0..1 how far to move
    "lut": null,                         // or your own .cube file (wins over match_reference)
    "eq": null                           // or manual: {"contrast": 1.1, "brightness": 0.02, "saturation": 1.2, "gamma": 1.0}
  },

  "captions": {
    "enabled": false,
    "font": "Montserrat:ExtraBold",      // bundled: Montserrat:<weight>, Inter:<weight>, Anton; or a system font name / .ttf path
    "size": 72,                          // px at the plan's width
    "color": "#FFFFFF",
    "highlight": "#FFE600",              // active-word colour
    "highlight_mode": "color",           // color (word turns highlight colour) | box (pill behind the word) | none
    "highlight_text": null,              // text colour on the box (box mode)
    "stroke": "#000000", "stroke_width": 6,
    "shadow": true,
    "bg": null,                          // "#000000B3" draws a box behind the whole line
    "case": "as-is",                     // as-is | upper | lower
    "strip_punct": false,
    "words_per_line": 3,                 // words per caption chunk (also breaks on pauses and punctuation)
    "max_width": 0.84,                   // wrap width as a fraction of frame width
    "y": 0.68,                           // vertical centre as a fraction of height
    "x": 0.5, "align": "center", "line_gap": 0.12
  },

  "texts": [                             // titles, headers, labels — same style keys as captions
    {"text": "3 rules for\nbetter sleep", "start": 0, "end": 3.5, "y": 0.2, "size": 80,
     "font": "Anton", "color": "#FFFFFF", "stroke_width": 0, "shadow": true, "bg": null, "case": "upper"}
  ],

  "overlays": [                          // B-roll and images, drawn under texts/captions
    {"file": "broll/city.mp4", "start": 4.0, "end": 6.2, "mode": "full", "in": 0},
    {"file": "img/chart.png", "start": 8.0, "end": 10.0, "x": 0.5, "y": 0.3, "w": 0.8}
  ],                                     // mode full = fills the frame; otherwise an inset w wide centred at x, y

  "audio": {
    "lufs": null,                        // default: the reference's loudness, else -14
    "voice_chain": "highpass=f=70,acompressor=threshold=-20dB:ratio=3:attack=5:release=150",
    "music": null,                       // a track the user owns or a royalty-free one; loops to length, fades out
    "music_db": -22,                     // bed level before ducking
    "duck": true                         // dip the music under the voice
  },

  "sfx": [ {"file": "sfx/whoosh.wav", "at": 4.0, "db": -8} ]
}
```

## Outputs in the work folder

- `timeline.json` segments, shots (raw in/out, output start, zoom), words on the output timeline
- `words_out.txt` the kept transcript, one line per sentence with output times
- `check_sheet.jpg` 18 frames of the render
- `grade.cube` the generated LUT (first line says what it does)
- `shots/` per-shot renders, cached; changing one shot only re-renders that shot
