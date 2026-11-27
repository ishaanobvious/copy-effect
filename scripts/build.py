#!/usr/bin/env python3
"""Render your footage in a reference video's style from one plan.json.

    python3 build.py plan.json --timeline   # cut + transcript only: prints the kept words
                                            # on the OUTPUT timeline so you can place texts,
                                            # overlays and sfx (all times are output seconds)
    python3 build.py plan.json --preview    # fast half-size render to check the look
    python3 build.py plan.json              # full render

Pipeline: (concat sources) → transcribe → cut pauses → split into shots with
alternating punch-ins → grade toward the reference → B-roll/image overlays →
text + word-highlight captions → voice chain, music bed with ducking, sfx →
loudness matched to the reference. See references/plan-format.md for every key.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import copy
import hashlib
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (color_stats, die, need, probe, run, sample_frames_rgb,  # noqa: E402
                    silences, transcribe)

SKILL = Path(__file__).resolve().parent.parent
FONTS = SKILL / "assets" / "fonts"

DEFAULTS: dict = {
    "format": {"width": 1080, "height": 1920, "fps": 30, "fit": "crop", "anchor": [0.5, 0.4]},
    "cut": {"mode": "auto", "noise_db": -35, "min_pause": 0.3, "keep_pause": 0.08,
            "start": None, "end": None, "remove": [], "segments": []},
    "shots": {"target_len": 2.5, "zooms": [1.0, 1.12], "anchor": None, "list": []},
    "speed": 1.0,
    "grade": {"match_reference": True, "strength": 0.6, "lut": None, "eq": None},
    "captions": {"enabled": False, "font": "Montserrat:ExtraBold", "size": 72, "color": "#FFFFFF",
                 "highlight": "#FFE600", "highlight_mode": "color", "stroke": "#000000",
                 "stroke_width": 6, "shadow": True, "case": "as-is", "strip_punct": False,
                 "words_per_line": 3, "max_width": 0.84, "y": 0.68, "bg": None, "line_gap": 0.12},
    "texts": [],
    "overlays": [],
    "audio": {"lufs": None, "voice_chain": "highpass=f=70,acompressor=threshold=-20dB:ratio=3:attack=5:release=150",
              "music": None, "music_db": -22, "duck": True},
    "sfx": [],
    "whisper_model": "base",
}


# ----------------------------------------------------------------- plan

def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge(out[k], v)
        else:
            out[k] = v
    return out


def load_plan(path: Path) -> dict:
    raw = json.loads(path.read_text())
    plan = merge(DEFAULTS, raw)
    root = path.parent

    def res(p):
        return None if p in (None, "") else str((root / p).expanduser().resolve()) if not Path(p).is_absolute() \
            else str(Path(p).expanduser())

    src = plan.get("source")
    if not src:
        die("plan needs \"source\" (a clip or a list of clips)")
    plan["source"] = [res(s) for s in (src if isinstance(src, list) else [src])]
    plan["output"] = res(plan.get("output") or "output.mp4")
    plan["reference"] = res(plan.get("reference"))
    plan["work"] = res(plan.get("work") or (Path(plan["output"]).stem + "_work"))
    if plan["grade"].get("lut"):
        plan["grade"]["lut"] = res(plan["grade"]["lut"])
    if plan["audio"].get("music"):
        plan["audio"]["music"] = res(plan["audio"]["music"])
    for o in plan["overlays"]:
        o["file"] = res(o["file"])
    for s in plan["sfx"]:
        s["file"] = res(s["file"])
    for t in plan["texts"]:
        t.setdefault("start", 0)
    for s in plan["source"]:
        if not Path(s).exists():
            die(f"source not found: {s}")
    return plan


def h(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:10]


# ----------------------------------------------------------------- source

def prepare_source(plan: dict, work: Path) -> Path:
    srcs = plan["source"]
    infos = [probe(s) for s in srcs]
    for s, i in zip(srcs, infos):
        if i["color_transfer"] in ("arib-std-b67", "smpte2084"):
            print(f"  ! {Path(s).name} is HDR ({i['color_transfer']}); colours may look washed out. "
                  "Export SDR from your phone/editor for best results.")
    if len(srcs) == 1 and infos[0]["has_audio"]:
        return Path(srcs[0])
    key = h([(s, Path(s).stat().st_mtime) for s in srcs])
    out = work / f"source_{key}.mkv"
    if out.exists():
        return out
    w, hh = infos[0]["width"], infos[0]["height"]
    fps = infos[0]["fps"]
    args, parts = [], []
    for i, (s, inf) in enumerate(zip(srcs, infos)):
        args += ["-i", s]
        if not inf["has_audio"]:
            args += ["-f", "lavfi", "-t", f"{inf['duration']:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    idx = 0
    for i, inf in enumerate(infos):
        a_idx = idx if inf["has_audio"] else idx + 1
        parts.append(f"[{idx}:v]scale={w}:{hh}:force_original_aspect_ratio=decrease,pad={w}:{hh}:(ow-iw)/2:(oh-ih)/2,"
                     f"setsar=1,fps={fps},format=yuv420p[v{i}];[{a_idx}:a]aresample=48000,"
                     f"aformat=channel_layouts=stereo[a{i}]")
        idx += 1 if inf["has_audio"] else 2
    chain = "".join(f"[v{i}][a{i}]" for i in range(len(srcs)))
    fc = ";".join(parts) + f";{chain}concat=n={len(srcs)}:v=1:a=1[v][a]"
    print(f"  joining {len(srcs)} clips")
    run(["ffmpeg", "-y", "-v", "error", *args, "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-c:a", "pcm_s16le", str(out)])
    return out


# ----------------------------------------------------------------- cutting

def subtract(ranges, holes):
    out = []
    for a, b in ranges:
        pieces = [(a, b)]
        for ha, hb in holes:
            nxt = []
            for pa, pb in pieces:
                if hb <= pa or ha >= pb:
                    nxt.append((pa, pb))
                else:
                    if ha > pa:
                        nxt.append((pa, ha))
                    if hb < pb:
                        nxt.append((hb, pb))
            pieces = nxt
        out += pieces
    return [(a, b) for a, b in out if b - a > 0.04]


def keep_segments(plan: dict, src: Path, dur: float, words) -> list[tuple[float, float]]:
    c = plan["cut"]
    start = c.get("start") or 0.0
    end = min(c.get("end") or dur, dur)
    mode = c["mode"]
    if mode == "auto":
        mode = "words" if words else "silence"
    if mode == "manual":
        segs = [(float(a), float(b)) for a, b in c["segments"]]
    elif mode == "none":
        segs = [(start, end)]
    elif mode == "words":
        if not words:
            die("cut mode 'words' needs a transcript (pip install openai-whisper)")
        pad = c["keep_pause"]
        segs, cur = [], None
        for w in words:
            a, b = max(start, w["s"] - pad), min(end, w["e"] + pad)
            if b <= start or a >= end:
                continue
            if cur and a - cur[1] < c["min_pause"] - 2 * pad:
                cur[1] = max(cur[1], b)
            else:
                if cur:
                    segs.append(tuple(cur))
                cur = [a, b]
        if cur:
            segs.append(tuple(cur))
    else:  # silence
        sil = silences(src, c["noise_db"], c["min_pause"])
        pad = c["keep_pause"]
        holes = [(a + pad, (b if b != float("inf") else dur) - pad) for a, b in sil]
        holes = [(a, b) for a, b in holes if b > a]
        segs = subtract([(start, end)], holes)
    segs = subtract([(max(start, a), min(end, b)) for a, b in segs if b > a], [tuple(r) for r in c["remove"]])
    if not segs:
        die("the cut removed everything; check cut.noise_db / cut.start / cut.end")
    return segs


def make_shots(plan: dict, segs, words) -> list[dict]:
    sp = plan["shots"]
    if sp.get("list"):
        return [{"in": float(s["in"]), "out": float(s["out"]), "zoom": float(s.get("zoom", 1.0)),
                 "anchor": s.get("anchor")} for s in sp["list"]]
    target = max(0.6, float(sp["target_len"]))
    zooms = sp["zooms"] or [1.0]
    pieces = []
    for a, b in segs:
        n = max(1, round((b - a) / target))
        cuts = []
        for k in range(1, n):
            want = a + (b - a) * k / n
            t = want
            if words:  # move the punch-in to the nearest gap between words
                gaps = [(w1["e"] + w2["s"]) / 2 for w1, w2 in zip(words, words[1:])
                        if a + 0.4 < (w1["e"] + w2["s"]) / 2 < b - 0.4]
                if gaps:
                    t = min(gaps, key=lambda g: abs(g - want))
            if (not cuts or t - cuts[-1] > 0.5) and a + 0.4 < t < b - 0.4:
                cuts.append(t)
        bounds = [a] + cuts + [b]
        pieces += [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    return [{"in": p[0], "out": p[1], "zoom": float(zooms[i % len(zooms)]), "anchor": None}
            for i, p in enumerate(pieces)]


def quantize(shots, fps: float, speed: float):
    cum = 0.0
    start_f = 0
    for s in shots:
        cum += (s["out"] - s["in"]) / speed
        end_f = max(start_f + 1, round(cum * fps))
        s["frames"] = end_f - start_f
        s["out_start"] = start_f / fps
        s["out_dur"] = s["frames"] / fps
        start_f = end_f
    return start_f / fps


def map_time(shots, t: float, speed: float):
    for s in shots:
        if s["in"] <= t < s["out"]:
            return s["out_start"] + (t - s["in"]) / speed
    return None


# ----------------------------------------------------------------- rendering shots

def atempo(speed: float) -> str:
    if abs(speed - 1) < 1e-3:
        return ""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return "," + ",".join(parts)


def even(x: float) -> int:
    return max(2, int(math.ceil(x / 2) * 2))


def vchain(sw: int, sh: int, W: int, H: int, fit: str, zoom: float, ax: float, ay: float) -> str:
    """filter_complex chain from [0:v] to [v] that fills W x H and applies the punch-in."""
    if fit == "crop":
        s = max(W / sw, H / sh) * zoom
        cw, ch = even(sw * s), even(sh * s)
        return (f"[0:v]scale={cw}:{ch}:flags=lanczos,crop={W}:{H}:{int((cw - W) * ax)}:{int((ch - H) * ay)},"
                f"setsar=1[v]")
    s_fit = min(W / sw, H / sh)
    fw, fh = even(sw * s_fit), even(sh * s_fit)
    if fit == "pad":
        base = (f"[0:v]scale={fw}:{fh}:flags=lanczos,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    else:  # blur
        s_cov = max(W / sw, H / sh)
        cw, ch = even(sw * s_cov), even(sh * s_cov)
        base = (f"[0:v]split[b][f];[b]scale={cw // 4 * 2}:{ch // 4 * 2},crop={W // 2}:{H // 2},boxblur=20:2,"
                f"scale={W}:{H},eq=brightness=-0.06[bg];[f]scale={fw}:{fh}:flags=lanczos[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    if zoom > 1.001:
        zw, zh = even(W * zoom), even(H * zoom)
        base += f",scale={zw}:{zh},crop={W}:{H}:{int((zw - W) * ax)}:{int((zh - H) * ay)}"
    return base + "[v]"


def render_shot(args):
    i, s, src, info, plan, W, H, fps, speed, out_dir, crf = args
    ax, ay = s.get("anchor") or plan["shots"].get("anchor") or plan["format"]["anchor"]
    key = h([str(src), s["in"], s["out"], s["zoom"], ax, ay, W, H, fps, speed, plan["format"]["fit"], s["frames"], crf])
    out = out_dir / f"shot_{i:04d}_{key}.mkv"
    if out.exists():
        return out
    dur = s["out_dur"]
    vc = vchain(info["width"], info["height"], W, H, plan["format"]["fit"], s["zoom"], ax, ay)
    vc = vc[:-3] + f",setpts=(PTS-STARTPTS)/{speed},fps={fps},format=yuv420p[v]"
    ac = (f"[0:a]asetpts=PTS-STARTPTS{atempo(speed)},aresample=48000,aformat=channel_layouts=stereo,apad,"
          f"afade=t=in:d=0.006,afade=t=out:st={max(0, dur - 0.008):.4f}:d=0.008[a]")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{s['in']:.4f}", "-t", f"{(s['out'] - s['in']) + 0.25:.4f}",
         "-i", str(src), "-filter_complex", f"{vc};{ac}", "-map", "[v]", "-map", "[a]",
         "-frames:v", str(s["frames"]), "-t", f"{dur:.5f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-g", "30",
         "-c:a", "pcm_s16le", str(out)])
    return out


# ----------------------------------------------------------------- grade

def build_lut(plan: dict, src: Path, dur: float, work: Path) -> str | None:
    g = plan["grade"]
    if g.get("lut"):
        return g["lut"]
    if not g.get("match_reference") or not plan.get("reference"):
        return None
    dna = json.loads(Path(plan["reference"]).read_text())
    ref = dna.get("color") or {}
    if not ref:
        return None
    src_stats = color_stats(sample_frames_rgb(src, dur, 16))
    k = float(g.get("strength", 0.6))
    contrast = min(1.35, max(0.75, ref["luma_std"] / max(src_stats["luma_std"], 1)))
    sat = min(1.6, max(0.5, ref["saturation_mean"] / max(src_stats["saturation_mean"], 1e-3)))
    shift = [max(-50, min(50, ref["rgb_mean"][c] - src_stats["rgb_mean"][c])) for c in range(3)]
    sm = src_stats["rgb_mean"]
    n = 33
    lines = [f"# copy-effect grade: contrast {contrast:.2f} sat {sat:.2f} shift {[round(x) for x in shift]} "
             f"strength {k}", f"LUT_3D_SIZE {n}"]
    for bi in range(n):
        for gi in range(n):
            for ri in range(n):
                v = [ri * 255 / (n - 1), gi * 255 / (n - 1), bi * 255 / (n - 1)]
                # weight by 4x(1-x): moves midtones, keeps pure black and white pinned (no milky lift)
                wt = [4 * (v[c] / 255) * (1 - v[c] / 255) for c in range(3)]
                v1 = [v[c] + ((v[c] - sm[c]) * (contrast - 1) + shift[c]) * wt[c] for c in range(3)]
                y = 0.2126 * v1[0] + 0.7152 * v1[1] + 0.0722 * v1[2]
                v2 = [y + (v1[c] - y) * sat for c in range(3)]
                o = [min(255, max(0, v[c] + k * (v2[c] - v[c]))) / 255 for c in range(3)]
                lines.append(f"{o[0]:.5f} {o[1]:.5f} {o[2]:.5f}")
    out = work / "grade.cube"
    out.write_text("\n".join(lines) + "\n")
    print(f"  grade: contrast ×{contrast:.2f}, saturation ×{sat:.2f}, RGB shift {[round(x) for x in shift]}, "
          f"strength {k}")
    return str(out)


# ----------------------------------------------------------------- text graphics

_font_cache: dict = {}


def find_font(spec: str, size: int):
    from PIL import ImageFont
    key = (spec, size)
    if key in _font_cache:
        return _font_cache[key]
    name, _, var = spec.partition(":")
    cand = []
    p = Path(name).expanduser()
    if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
        cand.append(p)
    low = name.lower().replace(" ", "")
    dirs = [FONTS, Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"), Path("/usr/share/fonts"), Path.home() / ".fonts",
            Path("C:/Windows/Fonts")]
    for d in dirs:
        if d.exists():
            for f in list(d.glob("*.tt[fc]")) + list(d.glob("*.otf")) + list(d.glob("*/*.tt[fc]")) + \
                    list(d.glob("*/*.otf")):
                if f.stem.lower().replace(" ", "").replace("-", "").startswith(low.replace("-", "")):
                    cand.append(f)
    if not cand:
        print(f"  ! font '{name}' not found, using Montserrat")
        cand = [FONTS / "Montserrat.ttf"]
        var = var or "ExtraBold"
    font = ImageFont.truetype(str(cand[0]), size)
    if var:
        try:
            font.set_variation_by_name(var)
        except Exception:
            pass
    _font_cache[key] = font
    return font


def rgba(c, default=(255, 255, 255, 255)):
    if not c:
        return default
    c = c.lstrip("#")
    if len(c) == 6:
        c += "FF"
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))


def draw_words(canvas, words, style: dict, W: int, H: int, k: float, hi: int | None = None):
    """Draw a caption/text block: `words` list, word `hi` highlighted."""
    from PIL import Image, ImageDraw, ImageFilter
    size = int(style.get("size", 72) * k)
    font = find_font(style.get("font", "Montserrat:ExtraBold"), size)
    sw = int(style.get("stroke_width", 0) * k)
    space = font.getlength(" ")
    maxw = style.get("max_width", 0.84) * W
    lines, cur, curw = [], [], 0.0
    for i, w in enumerate(words):
        ww = font.getlength(w)
        if cur and curw + space + ww > maxw:
            lines.append(cur)
            cur, curw = [], 0.0
        cur.append((i, w, ww))
        curw += (space if len(cur) > 1 else 0) + ww
    if cur:
        lines.append(cur)
    asc, desc = font.getmetrics()
    lh = asc + desc
    gap = int(lh * style.get("line_gap", 0.12))
    total = len(lines) * lh + (len(lines) - 1) * gap
    cx, cy = style.get("x", 0.5) * W, style.get("y", 0.5) * H
    y0 = cy - total / 2
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    col, hcol, scol = rgba(style.get("color")), rgba(style.get("highlight"), (255, 230, 0, 255)), \
        rgba(style.get("stroke"), (0, 0, 0, 255))
    mode = style.get("highlight_mode", "color")
    boxes = []
    for li, line in enumerate(lines):
        lw = sum(x[2] for x in line) + space * (len(line) - 1)
        align = style.get("align", "center")
        x = cx - lw / 2 if align == "center" else (cx if align == "left" else cx - lw)
        y = y0 + li * (lh + gap)
        for i, w, ww in line:
            boxes.append((i, w, x, y, ww))
            x += ww + space
    if style.get("bg"):
        pad = int(size * 0.28)
        xs = [b[2] for b in boxes] + [b[2] + b[4] for b in boxes]
        d.rounded_rectangle([min(xs) - pad, y0 - pad * 0.6, max(xs) + pad, y0 + total + pad * 0.6],
                            radius=int(size * 0.25), fill=rgba(style["bg"]))
    if hi is not None and mode == "box":
        for i, w, x, y, ww in boxes:
            if i == hi:
                p = int(size * 0.14)
                d.rounded_rectangle([x - p, y + lh * 0.06 - p * 0.5, x + ww + p, y + lh * 0.96 + p * 0.3],
                                    radius=int(size * 0.18), fill=hcol)
    if style.get("shadow"):
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        off = max(2, int(size * 0.05))
        for i, w, x, y, ww in boxes:
            sd.text((x + off, y + off), w, font=font, fill=(0, 0, 0, 150), stroke_width=sw, stroke_fill=(0, 0, 0, 150))
        sh = sh.filter(ImageFilter.GaussianBlur(max(2, size * 0.06)))
        canvas.alpha_composite(sh)
    for i, w, x, y, ww in boxes:
        fill = hcol if (hi == i and mode == "color") else col
        if hi == i and mode == "box":
            fill = rgba(style.get("highlight_text"), col)
        d.text((x, y), w, font=font, fill=fill, stroke_width=sw, stroke_fill=scol)
    canvas.alpha_composite(layer)


def caption_events(plan, shots, words, speed, k_case):
    cs = plan["captions"]
    out_words = []
    for w in words:
        s = map_time(shots, w["s"], speed)
        if s is None:
            continue
        e = map_time(shots, min(w["e"], w["s"] + 1.5) - 1e-3, speed)
        e = e if e is not None and e > s else s + 0.15
        t = w["w"]
        if cs.get("strip_punct"):
            t = re.sub(r"[^\w'’%$#@&+-]", "", t)
        t = t.upper() if k_case == "upper" else t.lower() if k_case == "lower" else t
        if t:
            out_words.append({"w": t, "s": s, "e": e, "raw": w["w"]})
    chunks, cur = [], []
    for i, w in enumerate(out_words):
        if cur and (len(cur) >= cs["words_per_line"] or w["s"] - cur[-1]["e"] > 0.35
                    or re.search(r"[.!?,;:]$", cur[-1]["raw"])):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    events = []
    for ci, ch in enumerate(chunks):
        end = ch[-1]["e"] + 0.3
        if ci + 1 < len(chunks):
            end = min(end, chunks[ci + 1][0]["s"])
        end = max(end, ch[0]["s"] + 0.12)
        texts = [w["w"] for w in ch]
        if cs.get("highlight_mode") in ("color", "box"):
            for wi, w in enumerate(ch):
                a = ch[0]["s"] if wi == 0 else w["s"]
                b = ch[wi + 1]["s"] if wi + 1 < len(ch) else end
                if b > a:
                    events.append({"s": a, "e": b, "kind": "caption", "words": texts, "hi": wi})
        else:
            events.append({"s": ch[0]["s"], "e": end, "kind": "caption", "words": texts, "hi": None})
    return events


def build_graphics(plan, shots, words, speed, total, W, H, fps, k, work: Path) -> Path | None:
    from PIL import Image
    events = []
    if plan["captions"].get("enabled"):
        if not words:
            print("  ! captions need a transcript (pip install openai-whisper); skipped")
        else:
            events += caption_events(plan, shots, words, speed, plan["captions"].get("case", "as-is"))
    for t in plan["texts"]:
        events.append({"s": float(t.get("start", 0)), "e": float(t.get("end", total)), "kind": "text", "style": t})
    if not events:
        return None
    cuts = sorted({0.0, total, *[min(max(e["s"], 0), total) for e in events],
                   *[min(max(e["e"], 0), total) for e in events]})
    gdir = work / "graphics"
    gdir.mkdir(exist_ok=True)
    blank = gdir / f"blank_{W}x{H}.png"
    if not blank.exists():
        Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    entries = []
    for a, b in zip(cuts, cuts[1:]):
        if b - a < 1e-3:
            continue
        mid = (a + b) / 2
        active = [e for e in events if e["s"] <= mid < e["e"]]
        if not active:
            entries.append((blank, b - a))
            continue
        key = h([[(e["kind"], e.get("words"), e.get("hi"), e.get("style")) for e in active],
                 plan["captions"], W, H])
        img = gdir / f"g_{key}.png"
        if not img.exists():
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            for e in active:
                if e["kind"] == "text":
                    st = e["style"]
                    txt = st.get("text", "")
                    txt = txt.upper() if st.get("case") == "upper" else txt
                    for li, line in enumerate(txt.split("\n")):
                        lst = dict(st)
                        size = st.get("size", 64)
                        lst["y"] = st.get("y", 0.1) + li * size * k * 1.15 / H
                        draw_words(canvas, line.split(" "), lst, W, H, k)
            for e in active:
                if e["kind"] == "caption":
                    draw_words(canvas, e["words"], plan["captions"], W, H, k, e["hi"])
            canvas.save(img)
        entries.append((img, b - a))
    lst = gdir / "track.txt"
    with lst.open("w") as f:
        for img, d in entries:
            f.write(f"file '{img}'\nduration {d:.5f}\n")
        f.write(f"file '{entries[-1][0]}'\n")
    out = work / "graphics.mov"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-vf", f"fps={fps},format=rgba", "-t", f"{total:.4f}", "-c:v", "png", str(out)])
    n_cap = sum(1 for e in events if e["kind"] == "caption")
    print(f"  graphics: {len(plan['texts'])} text(s), {n_cap} caption state(s), {len(entries)} slices")
    return out


# ----------------------------------------------------------------- final mix

def final_pass(plan, base: Path, gfx: Path | None, lut: str | None, total: float, W, H, fps, work: Path,
               out: Path, preview: bool):
    args = ["-i", str(base)]
    fc = []
    v = "[0:v]"
    g = plan["grade"]
    if lut:
        fc.append(f"{v}lut3d=file='{lut.replace(chr(92), '/').replace(':', chr(92) + ':')}'[vg]")
        v = "[vg]"
    elif g.get("eq"):
        e = g["eq"]
        fc.append(f"{v}eq=contrast={e.get('contrast', 1)}:brightness={e.get('brightness', 0)}:"
                  f"saturation={e.get('saturation', 1)}:gamma={e.get('gamma', 1)}[vg]")
        v = "[vg]"
    idx = 1
    for n, o in enumerate(plan["overlays"]):
        s, e = float(o["start"]), float(o["end"])
        is_img = Path(o["file"]).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
        if is_img:
            args += ["-loop", "1", "-t", f"{e - s + 0.1:.3f}", "-i", o["file"]]
            src = f"[{idx}:v]format=rgba"
        else:
            args += ["-i", o["file"]]
            src = (f"[{idx}:v]trim=start={float(o.get('in', 0)):.3f}:duration={e - s:.3f},"
                   f"setpts=PTS-STARTPTS,format=rgba")
        if o.get("mode") == "full":
            src += (f",scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}")
            x, y = "0", "0"
        else:
            ow = even(float(o.get("w", 0.8)) * W)
            src += f",scale={ow}:-2"
            x = f"{float(o.get('x', 0.5))}*W-w/2"
            y = f"{float(o.get('y', 0.35))}*H-h/2"
        fc.append(f"{src},setpts=PTS-STARTPTS+{s}/TB[o{n}]")
        fc.append(f"{v}[o{n}]overlay=x='{x}':y='{y}':enable='between(t,{s},{e})':eof_action=pass[vo{n}]")
        v = f"[vo{n}]"
        idx += 1
    if gfx:
        args += ["-i", str(gfx)]
        fc.append(f"{v}[{idx}:v]overlay=0:0:eof_action=pass[vt]")
        v = "[vt]"
        idx += 1
    fc.append(f"{v}format=yuv420p[vout]")

    a = plan["audio"]
    chain = a.get("voice_chain") or "anull"
    mix = []
    fc.append(f"[0:a]{chain}[voice]")
    voice = "[voice]"
    if a.get("music"):
        args += ["-stream_loop", "-1", "-i", a["music"]]
        fc.append(f"[{idx}:a]atrim=0:{total:.3f},aresample=48000,aformat=channel_layouts=stereo,"
                  f"volume={a.get('music_db', -22)}dB,afade=t=out:st={max(0, total - 1):.3f}:d=1[mus]")
        if a.get("duck", True):
            fc.append("[voice]asplit[voice1][sc]")
            fc.append("[mus][sc]sidechaincompress=threshold=0.02:ratio=6:attack=15:release=350[musd]")
            voice = "[voice1]"
            mix.append("[musd]")
        else:
            mix.append("[mus]")
        idx += 1
    for n, s in enumerate(plan["sfx"]):
        args += ["-i", s["file"]]
        ms = int(float(s["at"]) * 1000)
        fc.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={s.get('db', -6)}dB,"
                  f"adelay={ms}|{ms}[s{n}]")
        mix.append(f"[s{n}]")
        idx += 1
    if mix:
        fc.append(f"{voice}{''.join(mix)}amix=inputs={1 + len(mix)}:normalize=0:duration=first[aout]")
    else:
        fc.append(f"{voice}anull[aout]")

    mixfile = work / "mix.mkv"
    venc = ["-c:v", "libx264", "-preset", "veryfast" if preview else "medium", "-crf", "26" if preview else "18"]
    run(["ffmpeg", "-y", "-v", "error", *args, "-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "[aout]",
         "-t", f"{total:.4f}", "-r", str(fps), *venc, "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(mixfile)])

    # loudness: measure, then one linear gain + limiter (no pumping)
    target = a.get("lufs")
    if target is None and plan.get("reference"):
        try:
            target = json.loads(Path(plan["reference"]).read_text())["audio"].get("integrated_lufs")
        except Exception:
            target = None
    target = float(target if target is not None else -14)
    from common import loudness
    meas = loudness(mixfile).get("integrated_lufs")
    gain = 0.0 if meas is None or meas < -70 else max(-20, min(20, target - meas))
    out.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mixfile), "-c:v", "copy",
         "-af", f"volume={gain:.2f}dB,alimiter=limit=0.89:level=disabled", "-c:a", "aac", "-b:a", "192k",
         "-ar", "48000", "-movflags", "+faststart", str(out)])
    print(f"  audio: {meas} LUFS → target {target} LUFS (gain {gain:+.1f} dB)")


# ----------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("plan")
    ap.add_argument("--timeline", action="store_true", help="cut + transcript only, print output-time words")
    ap.add_argument("--preview", action="store_true", help="fast half-size render")
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    need("ffmpeg", "Install ffmpeg (brew install ffmpeg / apt install ffmpeg).")
    plan_path = Path(args.plan).resolve()
    plan = load_plan(plan_path)
    work = Path(plan["work"])
    work.mkdir(parents=True, exist_ok=True)
    fmt = plan["format"]
    k = 0.5 if args.preview else 1.0
    W, H = even(fmt["width"] * k), even(fmt["height"] * k)
    fps = float(fmt["fps"])
    speed = float(plan.get("speed") or 1.0)

    print("[1/6] source")
    src = prepare_source(plan, work)
    info = probe(src)
    dur = info["duration"]

    need_words = args.timeline or plan["captions"].get("enabled") or plan["cut"]["mode"] in ("auto", "words")
    words = None
    if need_words:
        print("[2/6] transcript")
        tr = transcribe(src, plan.get("whisper_model", "base"), work / f"transcript_{h(str(src))}.json")
        words = tr["words"] if tr else None
        if tr is None:
            print("  (no transcript: whisper missing; cutting on silence instead)")
    else:
        print("[2/6] transcript not needed")

    print("[3/6] cut + shots")
    segs = keep_segments(plan, src, dur, words)
    shots = make_shots(plan, segs, words)
    total = quantize(shots, fps, speed)
    kept = sum(b - a for a, b in segs)
    print(f"  {dur:.1f}s raw → {len(segs)} segments, {len(shots)} shots, {total:.1f}s out "
          f"(removed {dur - kept:.1f}s{', speed ×' + str(speed) if speed != 1 else ''})")
    tl = {"duration": total, "segments": segs, "shots": shots}
    if words:
        ow = []
        for w in words:
            t = map_time(shots, w["s"], speed)
            if t is not None:
                ow.append({"w": w["w"], "t": round(t, 2)})
        tl["words"] = ow
        txt = []
        line, line_t = [], None
        for w in ow:
            if line_t is None:
                line_t = w["t"]
            line.append(w["w"])
            if re.search(r"[.!?]$", w["w"]) or len(line) >= 12:
                txt.append(f"{line_t:6.2f}s  {' '.join(line)}")
                line, line_t = [], None
        if line:
            txt.append(f"{line_t:6.2f}s  {' '.join(line)}")
        (work / "words_out.txt").write_text("\n".join(txt) + "\n")
        (work / "words_out.json").write_text(json.dumps(ow))
    (work / "timeline.json").write_text(json.dumps(tl, indent=1, default=float))
    if args.timeline:
        if words:
            print("\n".join((work / "words_out.txt").read_text().splitlines()))
            print(f"\nword-level times: {work / 'words_out.json'}")
        print(f"timeline: {work / 'timeline.json'}")
        return

    print(f"[4/6] rendering {len(shots)} shots")
    shot_dir = work / ("shots_preview" if args.preview else "shots")
    shot_dir.mkdir(exist_ok=True)
    crf = 22 if args.preview else 14
    jobs = [(i, s, src, info, plan, W, H, fps, speed, shot_dir, crf) for i, s in enumerate(shots)]
    with cf.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        files = list(ex.map(render_shot, jobs))
    lst = work / "shots.txt"
    lst.write_text("".join(f"file '{f}'\n" for f in files))
    base = work / ("base_preview.mkv" if args.preview else "base.mkv")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(base)])

    print("[5/6] grade + graphics")
    lut = build_lut(plan, src, dur, work)
    gfx = build_graphics(plan, shots, words, speed, total, W, H, fps, k, work)

    print("[6/6] overlays, audio, encode")
    out = Path(plan["output"])
    if args.preview:
        out = out.with_name(out.stem + "_preview" + out.suffix)
    final_pass(plan, base, gfx, lut, total, W, H, fps, work, out, args.preview)

    sheet = work / "check_sheet.jpg"
    rate = 18 / max(total, 0.1)
    run(["ffmpeg", "-y", "-v", "error", "-i", str(out), "-vf",
         f"fps={rate:.5f},scale=240:-2,tile=6x3:padding=4:color=white", "-frames:v", "1", str(sheet)], check=False)
    print(f"\nDone → {out} ({total:.1f}s)\ncheck sheet → {sheet}")


if __name__ == "__main__":
    main()
