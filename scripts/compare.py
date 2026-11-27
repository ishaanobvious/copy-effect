#!/usr/bin/env python3
"""Score an edit against the reference it copies.

    python3 compare.py <reference_dir/style_dna.json> <your_edit.mp4> [--out compare_dir]

Measures the edit the same way analyze.py measured the reference, prints a
side-by-side table with a verdict per row, and writes compare.jpg (reference
contact sheet above, your edit below) so you can judge the look by eye.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import color_stats, loudness, pct, probe, run, sample_frames_rgb, scene_cuts  # noqa: E402


def measure(path: Path) -> dict:
    info = probe(path)
    dur = info["duration"]
    # build.py knows exactly where it cut; frame analysis can mistake hand/prop motion for jump cuts
    tl = path.with_name(path.stem.removesuffix("_preview") + "_work") / "timeline.json"
    if tl.exists():
        shots = json.loads(tl.read_text())["shots"]
        cuts = [s["out_start"] for s in shots[1:]]
    else:
        cuts = [c["t"] for c in scene_cuts(path) if 0.05 < c["t"] < dur - 0.05]
    b = [0.0] + cuts + [dur]
    lens = [b[i + 1] - b[i] for i in range(len(b) - 1)]
    col = color_stats(sample_frames_rgb(path, dur, 24))
    aud = loudness(path) if info["has_audio"] else {}
    return {
        "aspect": info["aspect"], "fps": info["fps"], "duration": round(dur, 1),
        "cuts_per_minute": round(len(cuts) / max(dur / 60, 1e-6), 1),
        "shot_len_median": round(pct(lens, 0.5), 2),
        "cuts_in_first_3s": sum(1 for c in cuts if c < 3),
        "luma_mean": col.get("luma_mean"), "luma_std": col.get("luma_std"),
        "saturation_mean": col.get("saturation_mean"), "warmth": col.get("warmth"),
        "integrated_lufs": aud.get("integrated_lufs"), "true_peak_dbfs": aud.get("true_peak_dbfs"),
    }


ROWS = [
    # key, label, tolerance (abs) or ratio tolerance (str "x%"), None = info only
    ("aspect", "aspect", "eq"),
    ("fps", "fps", 1.0),
    ("duration", "duration s", None),
    ("cuts_per_minute", "cuts / min", "35%"),
    ("shot_len_median", "median shot s", "35%"),
    ("cuts_in_first_3s", "cuts in first 3 s", 1),
    ("luma_mean", "brightness (0-255)", 15),
    ("luma_std", "contrast (luma std)", 10),
    ("saturation_mean", "saturation", 0.07),
    ("warmth", "warmth", 0.05),
    ("integrated_lufs", "loudness LUFS", 1.5),
    ("true_peak_dbfs", "true peak dBFS", None),
]


def verdict(ref, mine, tol):
    if tol is None or ref is None or mine is None:
        return ""
    if tol == "eq":
        return "ok" if ref == mine else "DIFF"
    if isinstance(tol, str) and tol.endswith("%"):
        r = float(tol[:-1]) / 100
        base = max(abs(ref), 1e-6)
        return "ok" if abs(mine - ref) / base <= r else ("HIGH" if mine > ref else "LOW")
    return "ok" if abs(mine - ref) <= tol else ("HIGH" if mine > ref else "LOW")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dna")
    ap.add_argument("edit")
    ap.add_argument("--out")
    args = ap.parse_args()
    dna_path = Path(args.dna)
    dna = json.loads(dna_path.read_text())
    edit = Path(args.edit)
    out = Path(args.out) if args.out else edit.with_name(edit.stem + "_compare")
    out.mkdir(parents=True, exist_ok=True)

    f, p, c, a = dna["format"], dna["pacing"], dna["color"], dna.get("audio", {})
    ref = {
        "aspect": f["aspect"], "fps": f["fps"], "duration": round(f["duration"], 1),
        "cuts_per_minute": p["cuts_per_minute"], "shot_len_median": p["shot_len_median"],
        "cuts_in_first_3s": p["cuts_in_first_3s"], "luma_mean": c.get("luma_mean"),
        "luma_std": c.get("luma_std"), "saturation_mean": c.get("saturation_mean"), "warmth": c.get("warmth"),
        "integrated_lufs": a.get("integrated_lufs"), "true_peak_dbfs": a.get("true_peak_dbfs"),
    }
    mine = measure(edit)
    lines = [f"{'':22}{'reference':>12}{'your edit':>12}   verdict", "-" * 56]
    misses = []
    for key, label, tol in ROWS:
        v = verdict(ref[key], mine[key], tol)
        if v not in ("", "ok"):
            misses.append(f"{label}: {v.lower()} ({mine[key]} vs {ref[key]})")
        lines.append(f"{label:22}{str(ref[key]):>12}{str(mine[key]):>12}   {v}")
    table = "\n".join(lines)
    print(table)
    print("\nOff target: " + ("; ".join(misses) if misses else "nothing — numbers match the reference"))
    print("Numbers can't see captions, graphics or framing — compare those on compare.jpg.")
    (out / "compare.txt").write_text(table + "\n")
    (out / "compare.json").write_text(json.dumps({"reference": ref, "edit": mine, "off_target": misses}, indent=2))

    ref_sheet = dna_path.parent / "contact_sheet.jpg"
    my_sheet = out / "edit_sheet.jpg"
    w = 240 if mine["aspect"] in ("9:16", "4:5") else 360
    rate = 24 / max(mine["duration"], 0.1)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(edit), "-vf",
         f"fps={rate:.5f},scale={w}:-2,tile=6x4:padding=4:color=white", "-frames:v", "1", str(my_sheet)])
    if ref_sheet.exists():
        try:
            from PIL import Image, ImageDraw
            A, B = Image.open(ref_sheet), Image.open(my_sheet)
            width = max(A.width, B.width)
            B2 = B.resize((width, int(B.height * width / B.width))) if B.width != width else B
            A2 = A.resize((width, int(A.height * width / A.width))) if A.width != width else A
            canvas = Image.new("RGB", (width, A2.height + B2.height + 80), "white")
            d = ImageDraw.Draw(canvas)
            d.text((10, 8), "REFERENCE", fill="black")
            canvas.paste(A2, (0, 30))
            d.text((10, A2.height + 50), "YOUR EDIT", fill="black")
            canvas.paste(B2, (0, A2.height + 75))
            canvas.save(out / "compare.jpg", quality=88)
            print(f"side by side → {out / 'compare.jpg'}")
        except ImportError:
            print(f"(install Pillow for compare.jpg) edit sheet → {my_sheet}")


if __name__ == "__main__":
    main()
