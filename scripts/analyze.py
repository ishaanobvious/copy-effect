#!/usr/bin/env python3
"""Measure a reference video's editing style.

    python3 analyze.py <reference.mp4 | URL> --out <dir> [--no-transcribe] [--model base]

Writes into <dir>:
  style_dna.json   every measured number (format, pacing, framing, colour, audio, speech)
  report.md        the same numbers as a readable summary for the style card
  contact_sheet.jpg  24 evenly spaced frames
  hook_sheet.jpg     the first 3 seconds at 4 fps (how the video opens)
  shots/shot_NNN.jpg one frame from the middle of each shot (max 48)
  transcript.json  word timestamps (when openai-whisper is installed)

Look at the sheets and shot frames yourself: captions, graphics, zooms and
transitions are judged by eye, the script only measures what numbers can.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (color_stats, die, loudness, need, pct, probe, rms_floor,  # noqa: E402
                    run, sample_frames_rgb, scene_cuts, silences, transcribe)


def fetch(src: str, out: Path) -> Path:
    if not re.match(r"^https?://", src):
        p = Path(src).expanduser()
        if not p.exists():
            die(f"reference not found: {src}")
        return p
    need("yt-dlp", "Install it (pip install yt-dlp) or download the reference yourself and pass the file.")
    out.mkdir(parents=True, exist_ok=True)
    tmpl = str(out / "reference.%(ext)s")
    run(["yt-dlp", "-f", "bv*+ba/b", "--merge-output-format", "mp4", "-o", tmpl, "--no-playlist", src],
        capture=True)
    files = sorted(out.glob("reference.*"))
    if not files:
        die("download finished but no file was written")
    return files[0]


def sheets(path: Path, info: dict, out: Path, shots: list[tuple[float, float]]) -> None:
    dur = info["duration"]
    w = 240 if info["width"] < info["height"] else 360
    n = 24
    rate = n / max(dur, 0.1)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vf",
         f"fps={rate:.5f},scale={w}:-2,tile=6x4:padding=4:color=white", "-frames:v", "1",
         str(out / "contact_sheet.jpg")])
    run(["ffmpeg", "-y", "-v", "error", "-t", "3", "-i", str(path), "-vf",
         f"fps=4,scale={w}:-2,tile=6x2:padding=4:color=white", "-frames:v", "1",
         str(out / "hook_sheet.jpg")])
    shot_dir = out / "shots"
    shot_dir.mkdir(exist_ok=True)
    step = max(1, len(shots) // 48 + (1 if len(shots) % 48 else 0)) if len(shots) > 48 else 1
    for i, (a, b) in enumerate(shots[::step]):
        t = (a + b) / 2
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale=540:-2", str(shot_dir / f"shot_{i + 1:03d}_{t:06.2f}s.jpg")])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference")
    ap.add_argument("--out", required=True)
    ap.add_argument("--scene", type=float, default=0.28, help="scene-change threshold (lower finds more cuts)")
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--model", default="base", help="whisper model for the transcript")
    args = ap.parse_args()

    need("ffmpeg", "Install ffmpeg (brew install ffmpeg / apt install ffmpeg).")
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    ref = fetch(args.reference, out)
    info = probe(ref)
    dur = info["duration"]
    print(f"[1/5] {ref.name}: {info['width']}x{info['height']} {info['aspect']} {info['fps']}fps {dur:.1f}s")

    print("[2/5] detecting cuts")
    found = [c for c in scene_cuts(ref, args.scene) if 0.05 < c["t"] < dur - 0.05]
    cuts = [c["t"] for c in found]
    hard = sum(1 for c in found if c["kind"] == "hard")
    bounds = [0.0] + cuts + [dur]
    shots = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    lens = [b - a for a, b in shots]
    hook_cuts = sum(1 for c in cuts if c < 3.0)
    thirds = []
    for k in range(3):
        a, b = dur * k / 3, dur * (k + 1) / 3
        thirds.append(round(sum(1 for c in cuts if a <= c < b) / max((b - a) / 60, 1e-6), 1))
    pacing = {
        "cuts": len(cuts),
        "hard_cuts": hard,
        "jump_cuts": len(cuts) - hard,
        "shots": len(shots),
        "cuts_per_minute": round(len(cuts) / max(dur / 60, 1e-6), 1),
        "shot_len_median": round(pct(lens, 0.5), 2),
        "shot_len_mean": round(sum(lens) / len(lens), 2),
        "shot_len_p10": round(pct(lens, 0.1), 2),
        "shot_len_p90": round(pct(lens, 0.9), 2),
        "shot_len_min": round(min(lens), 2),
        "shot_len_max": round(max(lens), 2),
        "cuts_in_first_3s": hook_cuts,
        "cuts_per_minute_by_third": thirds,
        "cut_times": found,
    }

    print("[3/5] colour")
    frames = sample_frames_rgb(ref, dur, 24)
    color = color_stats(frames)

    print("[4/5] audio")
    audio: dict = {"has_audio": info["has_audio"]}
    if info["has_audio"]:
        audio.update(loudness(ref))
        sil = [(a, min(b, dur)) for a, b in silences(ref, -35, 0.18)]
        gaps = [b - a for a, b in sil if a > 0.05 and b < dur - 0.05]
        audio["pauses_over_0_18s"] = len(gaps)
        audio["pause_mean_s"] = round(sum(gaps) / len(gaps), 2) if gaps else 0.0
        floor = rms_floor(ref)
        audio["rms_floor_dbfs"] = floor
        if floor is None:
            audio["music_bed"] = "unknown"
        elif floor < -55:
            audio["music_bed"] = "no (true silence between words)"
        elif floor > -30:
            audio["music_bed"] = "likely (sound never drops out)"
        else:
            audio["music_bed"] = "unknown (tight cuts or room tone hide it) - ask the user or listen"

    speech: dict = {}
    if info["has_audio"] and not args.no_transcribe:
        print("[5/5] transcript (whisper)")
        tr = transcribe(ref, args.model, out / "transcript.json")
        if tr is None:
            speech["note"] = "openai-whisper not installed; skipped (pip install openai-whisper)"
        elif tr["words"]:
            words = tr["words"]
            span = max(words[-1]["e"] - words[0]["s"], 1e-6)
            speech = {
                "language": tr["language"],
                "words": len(words),
                "words_per_minute": round(len(words) / span * 60, 1),
                "speech_starts_at": words[0]["s"],
                "first_line": " ".join(w["w"] for w in words if w["s"] < 3.0),
                "text": tr["text"],
            }
    else:
        print("[5/5] transcript skipped")

    sheets(ref, info, out, shots)

    dna = {
        "source": str(ref),
        "format": info,
        "pacing": pacing,
        "color": color,
        "audio": audio,
        "speech": speech,
        "shots": [{"i": i + 1, "start": round(a, 3), "end": round(b, 3), "len": round(b - a, 3)}
                  for i, (a, b) in enumerate(shots)],
    }
    (out / "style_dna.json").write_text(json.dumps(dna, indent=2))

    c = color
    lines = [
        f"# Reference DNA — {ref.name}",
        "",
        "## Format",
        f"- {info['width']}x{info['height']} ({info['aspect']}), {info['fps']} fps, {dur:.1f} s, "
        f"transfer {info['color_transfer']}",
        "",
        "## Pacing",
        f"- {pacing['cuts']} cuts ({hard} hard, {pacing['jump_cuts']} jump/punch-in or caption/graphic swap) → "
        f"{pacing['cuts_per_minute']} cuts/min; shot length median "
        f"{pacing['shot_len_median']} s (p10 {pacing['shot_len_p10']}, p90 {pacing['shot_len_p90']})",
        f"- Hook: {hook_cuts} cuts in the first 3 s; cuts/min by third: {thirds}",
        "",
        "## Colour",
        f"- Luma mean {c.get('luma_mean')} / std {c.get('luma_std')} (0–255), saturation {c.get('saturation_mean')}, "
        f"warmth {c.get('warmth')} (+ warm, − cool)",
        f"- Palette: {', '.join(p['hex'] + ' ' + str(int(p['share'] * 100)) + '%' for p in c.get('palette', [])[:6])}",
        "",
        "## Audio",
    ]
    if info["has_audio"]:
        lines += [
            f"- {audio.get('integrated_lufs')} LUFS integrated, LRA {audio.get('loudness_range_lu')} LU, "
            f"peak {audio.get('true_peak_dbfs')} dBFS",
            f"- Quiet floor {audio.get('rms_floor_dbfs')} dBFS (50 ms RMS, p2) → music bed: {audio.get('music_bed')}",
            f"- {audio.get('pauses_over_0_18s')} pauses over 0.18 s (mean {audio.get('pause_mean_s')} s)",
        ]
    else:
        lines.append("- no audio track")
    if speech.get("words"):
        lines += ["", "## Speech",
                  f"- {speech['words_per_minute']} words/min, speech starts at {speech['speech_starts_at']} s",
                  f"- Opening line: “{speech['first_line']}”"]
    lines += ["", "## Look at these next",
              "- contact_sheet.jpg (whole video), hook_sheet.jpg (first 3 s), shots/ (one frame per shot)",
              "- Judge by eye: captions (font, case, size, colour, position, words per line, highlight), "
              "framing and punch-ins, B-roll/graphics, transitions, text cards, hook structure, CTA."]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print(f"\nDone → {out}/report.md, style_dna.json, contact_sheet.jpg, hook_sheet.jpg, shots/")


if __name__ == "__main__":
    main()
