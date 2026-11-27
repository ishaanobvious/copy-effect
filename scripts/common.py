"""Shared helpers for copy-effect: ffmpeg/ffprobe wrappers and frame statistics.

Only the Python standard library is used here, so the analysis runs anywhere
ffmpeg is installed. Pillow is needed only for captions and contact sheets.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path


def die(msg: str, code: int = 1) -> None:
    print(f"copy-effect: {msg}", file=sys.stderr)
    sys.exit(code)


def need(binary: str, hint: str = "") -> str:
    path = shutil.which(binary)
    if not path:
        die(f"'{binary}' was not found on PATH. {hint}".strip())
    return path


def run(cmd: list[str], *, capture: bool = True, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=capture, text=True)
    if check and proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:]
        die(f"command failed ({proc.returncode}): {' '.join(cmd[:6])} ...\n{tail}")
    return proc


def probe(path: str | Path) -> dict:
    need("ffprobe", "Install ffmpeg (brew install ffmpeg / apt install ffmpeg).")
    out = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]).stdout
    data = json.loads(out)
    v = next((s for s in data["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        die(f"no video stream in {path}")
    w, h = int(v["width"]), int(v["height"])
    rot = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    if abs(rot) in (90, 270):
        w, h = h, w
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return {
        "width": w,
        "height": h,
        "fps": round(fps, 3),
        "duration": float(data["format"].get("duration") or v.get("duration") or 0),
        "has_audio": a is not None,
        "video_codec": v.get("codec_name"),
        "color_transfer": v.get("color_transfer", "unknown"),
        "aspect": aspect_name(w, h),
    }


def aspect_name(w: int, h: int) -> str:
    r = w / h
    for name, val in (("9:16", 9 / 16), ("4:5", 4 / 5), ("1:1", 1.0), ("16:9", 16 / 9), ("4:3", 4 / 3)):
        if abs(r - val) < 0.03:
            return name
    return f"{w}:{h}"


def sample_frames_rgb(path: str | Path, duration: float, count: int = 24, size: tuple[int, int] = (48, 48)):
    """Return a list of frames, each a list of (r,g,b) tuples, sampled evenly."""
    w, h = size
    frames = []
    count = max(1, count)
    for i in range(count):
        t = duration * (i + 0.5) / count
        proc = subprocess.run([
            "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
            "-vf", f"scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ], capture_output=True)
        buf = proc.stdout
        if len(buf) < w * h * 3:
            continue
        frames.append([(buf[j], buf[j + 1], buf[j + 2]) for j in range(0, w * h * 3, 3)])
    return frames


def color_stats(frames) -> dict:
    """Per-channel mean/std, luma, saturation and a small dominant palette."""
    n = 0
    s = [0.0, 0.0, 0.0]
    ss = [0.0, 0.0, 0.0]
    luma, luma2, sat = 0.0, 0.0, 0.0
    buckets: dict[tuple[int, int, int], int] = {}
    for fr in frames:
        for r, g, b in fr:
            n += 1
            for c, v in enumerate((r, g, b)):
                s[c] += v
                ss[c] += v * v
            y = 0.2126 * r + 0.7152 * g + 0.0722 * b
            luma += y
            luma2 += y * y
            mx, mn = max(r, g, b), min(r, g, b)
            sat += 0 if mx == 0 else (mx - mn) / mx
            key = (r >> 5, g >> 5, b >> 5)
            buckets[key] = buckets.get(key, 0) + 1
    if n == 0:
        return {}
    mean = [v / n for v in s]
    std = [math.sqrt(max(0.0, ss[c] / n - mean[c] ** 2)) for c in range(3)]
    lm = luma / n
    palette = []
    for (r, g, b), cnt in sorted(buckets.items(), key=lambda kv: -kv[1])[:8]:
        palette.append({
            "hex": "#%02x%02x%02x" % (r * 32 + 16, g * 32 + 16, b * 32 + 16),
            "share": round(cnt / n, 3),
        })
    warmth = (mean[0] - mean[2]) / 255
    return {
        "rgb_mean": [round(v, 2) for v in mean],
        "rgb_std": [round(v, 2) for v in std],
        "luma_mean": round(lm, 2),
        "luma_std": round(math.sqrt(max(0.0, luma2 / n - lm * lm)), 2),
        "saturation_mean": round(sat / n, 3),
        "warmth": round(warmth, 3),
        "palette": palette,
    }


def loudness(path: str | Path) -> dict:
    """Integrated loudness, range, true peak and the quiet floor (p10 momentary)."""
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", "ebur128=peak=true:metadata=0", "-f", "null", "-",
    ], capture_output=True, text=True)
    err = proc.stderr
    momentary = []
    for line in err.splitlines():
        if " M:" in line and "TARGET" in line:
            try:
                momentary.append(float(line.split(" M:")[1].split()[0]))
            except (IndexError, ValueError):
                pass
    summary = err.split("Summary:")[-1] if "Summary:" in err else ""

    def grab(label: str):
        for line in summary.splitlines():
            if line.strip().startswith(label):
                try:
                    return float(line.split(":")[1].split()[0])
                except (IndexError, ValueError):
                    return None
        return None

    vals = sorted(v for v in momentary if v > -120)
    floor = vals[int(len(vals) * 0.10)] if vals else None
    return {
        "integrated_lufs": grab("I:"),
        "loudness_range_lu": grab("LRA:"),
        "true_peak_dbfs": grab("Peak:"),
        "quiet_floor_lufs": round(floor, 1) if floor is not None else None,
    }


def rms_floor(path: str | Path) -> float | None:
    """2nd-percentile RMS over 50 ms windows (dBFS). Speech alone dips far below
    -50 between words; a music bed keeps it high. Ambiguous in the middle."""
    proc = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(path), "-vn", "-af",
        "aresample=16000,asetnsamples=n=800:p=0,astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-f", "null", "-",
    ], capture_output=True, text=True)
    vals = []
    for line in proc.stdout.splitlines():
        if "RMS_level=" in line:
            v = line.split("=", 1)[1].strip()
            if v not in ("-inf", "inf", "nan"):
                vals.append(float(v))
    if not vals:
        return None
    vals.sort()
    return round(vals[int(len(vals) * 0.02)], 1)


def silences(path: str | Path, noise_db: float = -35, min_dur: float = 0.25) -> list[tuple[float, float]]:
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
        "-af", f"silencedetect=noise={noise_db}dB:d={min_dur}", "-f", "null", "-",
    ], capture_output=True, text=True)
    out, start = [], None
    for line in proc.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            end = float(line.split("silence_end:")[1].split()[0].split("|")[0])
            out.append((max(0.0, start), end))
            start = None
    if start is not None:
        out.append((start, float("inf")))
    return out


def scene_cuts(path: str | Path, threshold: float = 0.28, jump_ratio: float = 5.0) -> list[dict]:
    """Find cuts from per-frame scene scores.

    A frame scoring over `threshold` is a hard cut (new shot). A jump cut on a
    locked-off talking head barely changes the picture, so it scores low; we
    catch it as a local spike: over 0.035 and `jump_ratio` times the median
    score of the surrounding second.
    """
    proc = subprocess.run([
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-an",
        "-vf", "scale=320:-2,select='gte(scene\\,0)',metadata=print:file=-", "-f", "null", "-",
    ], capture_output=True, text=True)
    times, scores = [], []
    t = None
    for line in proc.stdout.splitlines():
        if line.startswith("frame:") and "pts_time:" in line:
            t = float(line.split("pts_time:")[1].split()[0])
        elif line.startswith("lavfi.scene_score=") and t is not None:
            times.append(t)
            scores.append(float(line.split("=")[1]))
    cuts = []
    for i, (t, s) in enumerate(zip(times, scores)):
        win = scores[max(0, i - 15):i] + scores[i + 1:i + 16]
        med = sorted(win)[len(win) // 2] if win else 0.0
        local_max = all(s >= x for x in scores[max(0, i - 2):i + 3])
        if s > threshold and local_max:
            cuts.append({"t": round(t, 3), "kind": "hard", "score": round(s, 3)})
        elif s > 0.035 and s > jump_ratio * max(med, 0.004) and local_max:
            cuts.append({"t": round(t, 3), "kind": "jump", "score": round(s, 3)})
    # Merge cuts closer than 4 frames (flashes, dissolves read as one cut).
    merged: list[dict] = []
    for c in cuts:
        if not merged or c["t"] - merged[-1]["t"] > 0.13:
            merged.append(c)
        elif c["score"] > merged[-1]["score"]:
            merged[-1] = c
    return merged


def transcribe(path: str | Path, model: str = "base", cache: Path | None = None) -> dict | None:
    """Word-level transcript via openai-whisper, or None if it isn't installed."""
    if cache and cache.exists():
        return json.loads(cache.read_text())
    try:
        import whisper  # type: ignore
    except Exception:
        return None
    m = None
    cached = sorted((Path.home() / ".cache" / "whisper").glob("*.pt"))
    tries = [model] + [p.stem for p in cached if p.stem != model]
    for name in tries:
        try:
            m = whisper.load_model(name)
            if name != model:
                print(f"  whisper '{model}' unavailable, using cached '{name}'", file=sys.stderr)
            break
        except Exception as exc:  # usually a failed model download
            print(f"  whisper '{name}' could not load: {str(exc)[:120]}", file=sys.stderr)
    if m is None:
        print("  transcript skipped (no whisper model could load; see README troubleshooting)", file=sys.stderr)
        return None
    res = m.transcribe(str(path), word_timestamps=True, verbose=False)
    words = []
    for seg in res.get("segments", []):
        for w in seg.get("words", []):
            words.append({"w": w["word"].strip(), "s": round(w["start"], 3), "e": round(w["end"], 3)})
    data = {"language": res.get("language"), "text": res.get("text", "").strip(), "words": words}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, indent=1))
    return data


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)
