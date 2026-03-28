#!/usr/bin/env python3
"""
star_wars_intro_editor.py

A single-file Python 3 desktop app for generating a Star Wars–style intro video
with:
- Live preview inside the window
- Editable intro text / episode / title / crawl body
- Optional animated logo zoom before the crawl
- Optional background music with fade-in / fade-out
- More accurate perspective crawl using projective row mapping
- Visible save path in the UI and an Export button
- Save/load project JSON
- MP4 export

Dependencies
------------
pip install pillow numpy imageio imageio-ffmpeg

Notes
-----
- GUI uses tkinter (usually included with Python).
- For best typography, choose a bold TTF/OTF font file.
- Music muxing uses ffmpeg through imageio-ffmpeg. If muxing fails, the silent
  video remains available.
"""

import json
import math
import os
import shutil
import subprocess
import tempfile
import textwrap
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

# Default output directory: ../output relative to this script
_OUTPUT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output"))

# Default config directory: ../conf relative to this script
_CONF_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "conf"))
_DEFAULT_CONF = os.path.join(_CONF_DIR, "conf.json")

DEFAULTS = {
    "intro_text": "A long time ago in a lab far,\nfar away....",
    "episode_text": "EPISODE I",
    "title_text": "THE RISE OF CUSTOM WORDS",
    "crawl_text": (
        "It is a period of innovation.\n\n"
        "Creators across the galaxy are building intros, trailers, and epic openings "
        "for projects, podcasts, demos, and adventures.\n\n"
        "Now, a new title sequence emerges.\n\n"
        "With editable words, cinematic perspective, and a field of stars, "
        "this script enables anyone to render their own legendary opening crawl.\n\n"
        "The next chapter begins now...."
    ),
    "output_path": os.path.join(_OUTPUT_DIR, "star_wars_intro_v4.mp4"),
    "font_path": "",
    "logo_path": "",
    "music_path": "",
    "width": 1280,
    "height": 720,
    "fps": 24,
    "duration_intro": 6.0,
    "duration_logo": 4.5,
    "duration_crawl": 24.0,
    "intro_font_size": 28,
    "episode_font_size": 46,
    "title_font_size": 56,
    "body_font_size": 34,
    "star_count": 900,
    "crawl_speed": 80.0,
    "crawl_width_chars": 50,
    "crawl_depth": 1.10,
    "horizon_y": 0.30,
    "bottom_margin": 0.92,
    "near_width_frac": 1.05,
    "far_width_frac": 0.10,
    "line_spacing": 1.22,
    "paragraph_spacing": 0.55,
    "title_gap_scale": 0.90,
    "section_gap_scale": 2.60,
    "crawl_body_align": "justify",
    "intro_color": "#7ec8ff",
    "crawl_color": "#ffd54a",
    "bg_color": "#000000",
    "logo_bg_color": "#000000",
    "preview_scale": 0.34,
    "music_volume": 0.45,
    "music_fade_in": 2.0,
    "music_fade_out": 2.0,
    "logo_zoom_start": 0.72,
    "logo_zoom_end": 1.06,
}

# ----------------------------
# Helpers
# ----------------------------

def hex_to_rgb(value: str):
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return (255, 255, 255)
    return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def ease_in_out(t):
    t = clamp(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)

def ease_out_cubic(t):
    t = clamp(t, 0.0, 1.0)
    return 1.0 - (1.0 - t) ** 3

def safe_float(value, default):
    try:
        return float(value)
    except Exception:
        return default

def safe_int(value, default):
    try:
        return int(float(value))
    except Exception:
        return default

def load_font(font_path, size):
    try:
        if font_path and os.path.exists(font_path):
            return ImageFont.truetype(font_path, size=size)
    except Exception:
        pass
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except Exception:
        return ImageFont.load_default()

def text_bbox(draw, text, font):
    if not text:
        return (0, 0, 0, 0)
    return draw.multiline_textbbox((0, 0), text, font=font, spacing=0, align="center")

def add_vignette(np_img, strength=0.18):
    h, w = np_img.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2.0, h / 2.0
    nx = (xx - cx) / (w / 2.0)
    ny = (yy - cy) / (h / 2.0)
    rr = np.sqrt(nx * nx + ny * ny)
    mask = 1.0 - strength * np.clip(rr, 0.0, 1.0)
    out = (np_img.astype(np.float32) * mask[..., None]).clip(0, 255).astype(np.uint8)
    return out

# ----------------------------
# Scene assets
# ----------------------------

def generate_starfield(width, height, star_count, bg_rgb):
    img = np.zeros((height, width, 3), dtype=np.uint8)
    img[:, :] = np.array(bg_rgb, dtype=np.uint8)

    rng = np.random.default_rng(12345)
    xs = rng.integers(0, width, size=star_count)
    ys = rng.integers(0, height, size=star_count)
    intensities = rng.integers(140, 256, size=star_count)
    radii = rng.choice([1, 1, 1, 2], size=star_count)

    for x, y, iv, r in zip(xs, ys, intensities, radii):
        if r == 1:
            img[y, x] = (iv, iv, iv)
        else:
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < width and 0 <= yy < height:
                        d2 = dx * dx + dy * dy
                        falloff = max(0.0, 1.0 - d2 / ((r + 0.35) ** 2))
                        val = int(iv * falloff)
                        img[yy, xx] = np.maximum(img[yy, xx], (val, val, val))
    return img

def wrap_paragraphs(text, width_chars=30):
    paragraphs = [p.strip() for p in text.split("\n")]
    out = []
    for p in paragraphs:
        if not p:
            out.append("")
            continue
        out.extend(textwrap.wrap(p, width=width_chars, break_long_words=False, replace_whitespace=False))
    return "\n".join(out)

def build_crawl_texture(cfg):
    dummy = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy)

    font_path = cfg["font_path"]
    episode_font = load_font(font_path, cfg["episode_font_size"])
    title_font = load_font(font_path, cfg["title_font_size"])
    body_font = load_font(font_path, cfg["body_font_size"])

    wrapped_body = wrap_paragraphs(cfg["crawl_text"], width_chars=cfg["crawl_width_chars"])

    episode = cfg["episode_text"].strip()
    title = cfg["title_text"].strip()
    body = wrapped_body.strip()

    ep_bbox = text_bbox(draw, episode, episode_font)
    tt_bbox = text_bbox(draw, title, title_font)
    body_bbox = text_bbox(draw, body, body_font)

    ep_w = ep_bbox[2] - ep_bbox[0]
    ep_h = ep_bbox[3] - ep_bbox[1]
    tt_w = tt_bbox[2] - tt_bbox[0]
    tt_h = tt_bbox[3] - tt_bbox[1]
    body_w = body_bbox[2] - body_bbox[0]

    # Compute actual rendered body height using the same formula as the drawing loop,
    # so the texture is never shorter than the content (text_bbox uses spacing=0
    # and doesn't account for line_spacing or paragraph_spacing).
    _body_line_h = max(1, int(cfg["body_font_size"] * cfg["line_spacing"]))
    _para_extra = int(cfg["body_font_size"] * cfg["paragraph_spacing"])
    body_h = sum(
        _body_line_h if ln.strip() else _body_line_h + _para_extra
        for ln in wrapped_body.split("\n")
    )

    width = int(max(ep_w, tt_w, body_w) + 160)
    title_gap = int(cfg["title_font_size"] * cfg["title_gap_scale"])    # gap between episode and title
    section_gap = int(cfg["body_font_size"] * cfg["section_gap_scale"]) # gap between title and crawl body

    # Trailing blank rows = same height as the render window so the last text line
    # scrolls completely off the visible trapezoid before the texture runs out.
    _frame_h = cfg.get("height", 720)
    _visible_h = max(10, int(_frame_h * (cfg["bottom_margin"] - cfg["horizon_y"])))
    trailing_pad = max(300, int(_visible_h * cfg["crawl_depth"] * 2.2))

    height = int(ep_h + title_gap + tt_h + section_gap + body_h + 180 + trailing_pad)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fill = hex_to_rgb(cfg["crawl_color"])

    y = 54
    d.multiline_text((width // 2, y), episode, font=episode_font, fill=fill, anchor="ma", align="center")
    y += ep_h + title_gap
    d.multiline_text((width // 2, y), title, font=title_font, fill=fill, anchor="ma", align="center")
    y += tt_h + section_gap

    body_lines = wrapped_body.split("\n")
    body_line_height = max(1, int(cfg["body_font_size"] * cfg["line_spacing"]))
    para_extra = int(cfg["body_font_size"] * cfg["paragraph_spacing"])

    body_align = cfg.get("crawl_body_align", "justify")
    left_x = 80
    right_x = width - 80
    text_width = right_x - left_x

    for i, line in enumerate(body_lines):
        if line.strip():
            if body_align == "justify":
                # Last line of a paragraph stays left-aligned
                is_last = (i == len(body_lines) - 1) or not body_lines[i + 1].strip()
                words = line.split()
                if is_last or len(words) <= 1:
                    d.text((left_x, y), line, font=body_font, fill=fill, anchor="la")
                else:
                    word_widths = [d.textbbox((0, 0), w, font=body_font)[2] - d.textbbox((0, 0), w, font=body_font)[0] for w in words]
                    gap = (text_width - sum(word_widths)) / (len(words) - 1)
                    x = float(left_x)
                    for word, ww in zip(words, word_widths):
                        d.text((int(x), y), word, font=body_font, fill=fill, anchor="la")
                        x += ww + gap
            elif body_align == "right":
                d.text((right_x, y), line, font=body_font, fill=fill, anchor="ra")
            elif body_align == "center":
                d.text((width // 2, y), line, font=body_font, fill=fill, anchor="ma")
            else:  # left
                d.text((left_x, y), line, font=body_font, fill=fill, anchor="la")
            y += body_line_height
        else:
            y += body_line_height + para_extra

    return np.array(img)


# ----------------------------
# Homography helpers
# ----------------------------

def find_perspective_coeffs(pa, pb):
    """
    Return coefficients for PIL.Image.transform(Image.PERSPECTIVE, coeffs)
    mapping output quadrilateral pa -> input quadrilateral pb.

    pa: list of 4 destination points [(x0,y0), (x1,y1), (x2,y2), (x3,y3)]
    pb: list of 4 source points      [(u0,v0), (u1,v1), (u2,v2), (u3,v3)]
    """
    matrix = []
    for (x, y), (u, v) in zip(pa, pb):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])

    A = np.array(matrix, dtype=np.float64)
    B = np.array(pb, dtype=np.float64).reshape(8)
    coeffs = np.linalg.solve(A, B)
    return coeffs.tolist()


def extract_vertical_window_rgba(tex_rgba, y0, window_h):
    """
    Extract a vertical RGBA window from the crawl texture with transparent padding.
    y0 can be fractional or outside the texture; the returned image is exactly window_h high.
    """
    tex_h, tex_w = tex_rgba.shape[0], tex_rgba.shape[1]
    y0i = int(math.floor(y0))
    y1i = y0i + int(window_h)

    out = np.zeros((int(window_h), tex_w, 4), dtype=np.uint8)

    src_top = max(0, y0i)
    src_bot = min(tex_h, y1i)
    if src_bot <= src_top:
        return Image.fromarray(out, "RGBA")

    dst_top = src_top - y0i
    dst_bot = dst_top + (src_bot - src_top)
    out[dst_top:dst_bot] = tex_rgba[src_top:src_bot]
    return Image.fromarray(out, "RGBA")

# ----------------------------
# Rendering
# ----------------------------

def render_intro_frame(cfg, starfield, t):
    width = cfg["width"]
    height = cfg["height"]
    duration = cfg["duration_intro"]

    img = Image.fromarray(starfield.copy())
    font = load_font(cfg["font_path"], cfg["intro_font_size"])
    fill = hex_to_rgb(cfg["intro_color"])

    fade_in_end = duration * 0.25
    fade_out_start = duration * 0.72

    if t < fade_in_end:
        alpha = ease_in_out(t / max(fade_in_end, 1e-6))
    elif t > fade_out_start:
        alpha = 1.0 - ease_in_out((t - fade_out_start) / max(duration - fade_out_start, 1e-6))
    else:
        alpha = 1.0
    alpha = clamp(alpha, 0.0, 1.0)

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    text = cfg["intro_text"]

    bbox = od.multiline_textbbox((0, 0), text, font=font, align="center", spacing=8)
    th = bbox[3] - bbox[1]
    x = width // 2
    y = height // 2 - th // 2
    rgba = (*fill, int(255 * alpha))
    od.multiline_text((x, y), text, font=font, fill=rgba, anchor="ma", align="center", spacing=8)

    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return add_vignette(np.array(img), strength=0.10)

def render_logo_frame(cfg, t):
    width = cfg["width"]
    height = cfg["height"]
    duration = max(cfg["duration_logo"], 1e-6)

    base = np.zeros((height, width, 3), dtype=np.uint8)
    base[:, :] = np.array(hex_to_rgb(cfg["logo_bg_color"]), dtype=np.uint8)
    img = Image.fromarray(base).convert("RGBA")

    alpha = 1.0
    fade_in_end = duration * 0.18
    fade_out_start = duration * 0.76
    if t < fade_in_end:
        alpha = ease_in_out(t / max(fade_in_end, 1e-6))
    elif t > fade_out_start:
        alpha = 1.0 - ease_in_out((t - fade_out_start) / max(duration - fade_out_start, 1e-6))
    alpha = clamp(alpha, 0.0, 1.0)

    zt = ease_out_cubic(t / duration)
    zoom = cfg["logo_zoom_start"] + (cfg["logo_zoom_end"] - cfg["logo_zoom_start"]) * zt

    logo_path = cfg["logo_path"].strip()
    if logo_path and os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            lw, lh = logo.size
            base_scale = min(width * 0.62 / max(lw, 1), height * 0.42 / max(lh, 1), 10.0)
            scale = base_scale * zoom
            new_size = (max(1, int(lw * scale)), max(1, int(lh * scale)))
            logo = logo.resize(new_size, Image.LANCZOS)

            arr = np.array(logo)
            arr[:, :, 3] = (arr[:, :, 3].astype(np.float32) * alpha).clip(0, 255).astype(np.uint8)
            logo = Image.fromarray(arr, "RGBA")

            x = (width - logo.width) // 2
            y = (height - logo.height) // 2
            img.alpha_composite(logo, (x, y))
        except Exception:
            pass
    else:
        draw = ImageDraw.Draw(img)
        font = load_font(cfg["font_path"], max(40, cfg["title_font_size"]))
        fill = (*hex_to_rgb(cfg["crawl_color"]), int(255 * alpha))
        draw.text((width // 2, height // 2), "LOGO", font=font, fill=fill, anchor="mm")

    return add_vignette(np.array(img.convert("RGB")), strength=0.05)

def render_crawl_frame(cfg, starfield, crawl_texture, t):
    """
    True projective crawl:
    - Take a vertical window from the crawl texture.
    - Warp that rectangular window into a trapezoid with a perspective transform.
    - Composite onto the starfield.

    This replaces the older scanline-by-scanline pseudo-perspective warp.
    """
    width = cfg["width"]
    height = cfg["height"]

    horizon_y = int(height * cfg["horizon_y"])
    bottom_y = int(height * cfg["bottom_margin"])
    visible_h = max(10, bottom_y - horizon_y)

    near_w = int(width * cfg["near_width_frac"])
    far_w = max(4, int(width * cfg["far_width_frac"]))

    tex = crawl_texture
    tex_h, tex_w = tex.shape[0], tex.shape[1]

    # The texture window that is currently visible on the receding plane.
    # As time increases, we move downward through the texture so the crawl rises.
    speed = cfg["crawl_speed"]  # texture rows per second
    scroll = speed * t

    # This controls how much texture height is packed into the visible trapezoid.
    # Larger values make the crawl feel more compressed into the distance.
    depth = max(cfg["crawl_depth"], 0.1)
    window_h = max(32, int(visible_h * depth * 2.2))

    # Position window so text enters from the bottom at t=0 and scrolls upward.
    # At t=0: y0 = -window_h → bottom of screen sees texture row 0 (episode title).
    # As scroll increases, the window rises → text moves from bottom toward horizon.
    y0 = scroll - window_h
    window_img = extract_vertical_window_rgba(tex, y0, window_h)

    # Destination trapezoid in full-frame coordinates.
    cx = width / 2.0
    quad_dst = [
        (cx - far_w / 2.0, float(horizon_y)),  # top-left
        (cx + far_w / 2.0, float(horizon_y)),  # top-right
        (cx + near_w / 2.0, float(bottom_y)),  # bottom-right
        (cx - near_w / 2.0, float(bottom_y)),  # bottom-left
    ]

    # Warp onto a full-frame transparent canvas so the perspective is globally correct.
    src_rect = [
        (0.0, 0.0),
        (float(tex_w), 0.0),
        (float(tex_w), float(window_h)),
        (0.0, float(window_h)),
    ]
    coeffs = find_perspective_coeffs(quad_dst, src_rect)

    warped = window_img.transform(
        (width, height),
        Image.PERSPECTIVE,
        coeffs,
        resample=Image.BICUBIC,
        fillcolor=(0, 0, 0, 0),
    )

    # Fade text as it approaches the horizon by multiplying alpha in the top band.
    warped_arr = np.array(warped, dtype=np.uint8)
    fade_top_end = min(height, horizon_y + int(visible_h * 0.16))
    if fade_top_end > horizon_y:
        for sy in range(horizon_y, fade_top_end):
            a = (sy - horizon_y) / max(1, fade_top_end - horizon_y)
            warped_arr[sy, :, 3] = (warped_arr[sy, :, 3].astype(np.float32) * a).astype(np.uint8)

    # Fade out the entire crawl layer during the last 3 seconds of duration_crawl.
    crawl_duration = max(cfg["duration_crawl"], 1.0)
    fade_out_dur = min(3.0, crawl_duration * 0.25)
    fade_out_start = crawl_duration - fade_out_dur
    if t >= fade_out_start:
        crawl_alpha = 1.0 - ease_in_out((t - fade_out_start) / max(fade_out_dur, 1e-6))
        crawl_alpha = clamp(crawl_alpha, 0.0, 1.0)
        warped_arr[:, :, 3] = (warped_arr[:, :, 3].astype(np.float32) * crawl_alpha).astype(np.uint8)

    # Composite onto the starfield.
    bg = Image.fromarray(starfield.copy()).convert("RGBA")
    fg = Image.fromarray(warped_arr, "RGBA")
    out = Image.alpha_composite(bg, fg).convert("RGB")

    # Mild glow by blending a slightly shifted copy.
    frame = np.array(out)
    glow = frame.astype(np.float32)
    glow[1:] = np.maximum(glow[1:], frame[:-1].astype(np.float32) * 0.06)
    frame = np.clip(glow, 0, 255).astype(np.uint8)

    return add_vignette(frame, strength=0.16)

def render_frame(cfg, t, starfield, crawl_texture):
    intro_end = cfg["duration_intro"]
    logo_end = intro_end + cfg["duration_logo"]

    if t < intro_end:
        return render_intro_frame(cfg, starfield, t)
    elif t < logo_end and cfg["duration_logo"] > 0:
        return render_logo_frame(cfg, t - intro_end)
    else:
        crawl_t = t - intro_end - cfg["duration_logo"]
        return render_crawl_frame(cfg, starfield, crawl_texture, crawl_t)

# ----------------------------
# Export / audio
# ----------------------------

def ffmpeg_exe():
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return shutil.which("ffmpeg")

def mux_music(video_path, music_path, output_path, volume=0.45, fade_in=2.0, fade_out=2.0):
    exe = ffmpeg_exe()
    if not exe:
        raise RuntimeError("ffmpeg executable not found for audio muxing.")

    # Need duration for fade-out
    reader = imageio.get_reader(video_path)
    meta = reader.get_meta_data()
    fps = meta.get("fps", 24)
    nframes = meta.get("nframes", None)
    reader.close()

    duration = None
    if nframes and isinstance(nframes, (int, float)) and nframes > 0:
        duration = float(nframes) / float(fps)
    else:
        # fall back to ffprobe-like inference via imageio metadata; if missing, skip fade-out timing
        duration = None

    filt = [f"volume={float(volume):.4f}"]
    if fade_in > 0:
        filt.append(f"afade=t=in:st=0:d={float(fade_in):.4f}")
    if fade_out > 0 and duration and duration > fade_out:
        st = max(0.0, duration - float(fade_out))
        filt.append(f"afade=t=out:st={st:.4f}:d={float(fade_out):.4f}")

    cmd = [
        exe,
        "-y",
        "-i", video_path,
        "-stream_loop", "-1",
        "-i", music_path,
        "-filter:a", ",".join(filt),
        "-shortest",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        output_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-2000:])

def render_video(cfg, progress_cb=None):
    width = cfg["width"]
    height = cfg["height"]
    fps = cfg["fps"]
    total_duration = cfg["duration_intro"] + cfg["duration_logo"] + cfg["duration_crawl"]
    total_frames = max(1, int(round(total_duration * fps)))

    starfield = generate_starfield(width, height, cfg["star_count"], hex_to_rgb(cfg["bg_color"]))
    crawl_texture = build_crawl_texture(cfg)

    output_path = cfg["output_path"]
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    tmp_video_path = output_path
    silent_fallback_path = None
    needs_mux = bool(cfg["music_path"].strip() and os.path.exists(cfg["music_path"].strip()))
    tmp_mux_src = None

    if needs_mux:
        fd, tmp_mux_src = tempfile.mkstemp(prefix="sw_intro_silent_", suffix=".mp4")
        os.close(fd)
        tmp_video_path = tmp_mux_src
        silent_fallback_path = output_path.rsplit(".", 1)[0] + "_silent.mp4"

    writer = imageio.get_writer(
        tmp_video_path,
        fps=fps,
        codec="libx264",
        quality=8,
        pixelformat="yuv420p",
    )

    try:
        for i in range(total_frames):
            t = i / fps
            frame = render_frame(cfg, t, starfield, crawl_texture)
            writer.append_data(frame)
            if progress_cb:
                progress_cb(i + 1, total_frames, "video")
    finally:
        writer.close()

    if needs_mux:
        try:
            if silent_fallback_path:
                shutil.copy2(tmp_video_path, silent_fallback_path)
            if progress_cb:
                progress_cb(total_frames, total_frames, "audio")
            mux_music(
                tmp_video_path,
                cfg["music_path"].strip(),
                output_path,
                cfg["music_volume"],
                cfg["music_fade_in"],
                cfg["music_fade_out"],
            )
        finally:
            try:
                os.remove(tmp_video_path)
            except Exception:
                pass

# ----------------------------
# Preview
# ----------------------------

def make_preview_image(cfg, preview_t):
    width = cfg["width"]
    height = cfg["height"]
    starfield = generate_starfield(width, height, cfg["star_count"], hex_to_rgb(cfg["bg_color"]))
    crawl_texture = build_crawl_texture(cfg)
    arr = render_frame(cfg, preview_t, starfield, crawl_texture)
    return Image.fromarray(arr)

# ----------------------------
# UI
# ----------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Star Wars Intro Editor v4")
        self.root.geometry("1420x940")

        self.vars = {}
        self.rendering = False
        self.preview_after_id = None
        self.preview_image_ref = None

        self._build_ui()
        self._load_defaults()
        self.schedule_preview()

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        left = ttk.Frame(outer)
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(outer, width=430)
        right.pack(side="right", fill="y")
        right.pack_propagate(False)

        preview_group = ttk.LabelFrame(left, text="Live preview")
        preview_group.pack(fill="x", pady=(0, 10))

        self.preview_label = ttk.Label(preview_group)
        self.preview_label.pack(padx=10, pady=10)

        controls = ttk.Frame(preview_group)
        controls.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(controls, text="Preview time").pack(side="left")
        self.preview_time_var = tk.DoubleVar(value=2.0)
        self.preview_slider = ttk.Scale(
            controls, from_=0.0, to=34.5, variable=self.preview_time_var,
            command=lambda _=None: self.update_preview_now()
        )
        self.preview_slider.pack(side="left", fill="x", expand=True, padx=10)
        self.preview_time_label = ttk.Label(controls, text="2.00 s")
        self.preview_time_label.pack(side="left")

        ttk.Label(left, text="Intro text").pack(anchor="w")
        self.intro_text = tk.Text(left, height=4, wrap="word")
        self.intro_text.pack(fill="x", pady=(0, 8))

        ttk.Label(left, text="Episode text").pack(anchor="w")
        self.episode_text = tk.Text(left, height=2, wrap="word")
        self.episode_text.pack(fill="x", pady=(0, 8))

        ttk.Label(left, text="Title text").pack(anchor="w")
        self.title_text = tk.Text(left, height=3, wrap="word")
        self.title_text.pack(fill="x", pady=(0, 8))

        ttk.Label(left, text="Crawl body").pack(anchor="w")
        self.crawl_text = tk.Text(left, height=18, wrap="word")
        self.crawl_text.pack(fill="both", expand=True, pady=(0, 10))

        for widget in [self.intro_text, self.episode_text, self.title_text, self.crawl_text]:
            widget.bind("<<Modified>>", self.on_text_modified)

        bottom_actions = ttk.Frame(left)
        bottom_actions.pack(fill="x")

        ttk.Button(bottom_actions, text="Load Project JSON", command=self.load_project).pack(side="left", padx=(0, 8))
        ttk.Button(bottom_actions, text="Save Project JSON", command=self.save_project).pack(side="left", padx=(0, 8))
        ttk.Button(bottom_actions, text="Refresh Preview", command=self.update_preview_now).pack(side="left", padx=(0, 8))
        self._export_btn1 = ttk.Button(bottom_actions, text="Export MP4", command=self.start_render)
        self._export_btn1.pack(side="left", padx=(0, 8))
        self.progress = ttk.Progressbar(bottom_actions, orient="horizontal", mode="determinate", length=160)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.status = ttk.Label(bottom_actions, text="Ready", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)

        savebar = ttk.LabelFrame(left, text="Export")
        savebar.pack(fill="x", pady=(10, 0))

        path_row = ttk.Frame(savebar)
        path_row.pack(fill="x", padx=8, pady=8)

        ttk.Label(path_row, text="Save path").pack(side="left")
        self.output_path_var = tk.StringVar()
        self.vars["output_path"] = self.output_path_var
        out_entry = ttk.Entry(path_row, textvariable=self.output_path_var)
        out_entry.pack(side="left", fill="x", expand=True, padx=8)
        out_entry.bind("<KeyRelease>", lambda e: self.schedule_preview())
        out_entry.bind("<FocusOut>", lambda e: self.schedule_preview())
        ttk.Button(path_row, text="Browse", command=lambda: self.browse_path("output_path")).pack(side="left", padx=(0, 8))
        self._export_btn2 = ttk.Button(path_row, text="Export", command=self.start_render)
        self._export_btn2.pack(side="left")

        canvas = tk.Canvas(right, highlightthickness=0)
        scrollbar = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        settings_frame = ttk.Frame(canvas)
        settings_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=settings_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        fields = [
            ("font_path", "Font path (TTF/OTF)"),
            ("logo_path", "Logo image path"),
            ("music_path", "Background music path"),
            ("width", "Width"),
            ("height", "Height"),
            ("fps", "FPS"),
            ("duration_intro", "Intro duration (s)"),
            ("duration_logo", "Logo duration (s)"),
            ("duration_crawl", "Crawl duration (s)"),
            ("intro_font_size", "Intro font size"),
            ("episode_font_size", "Episode font size"),
            ("title_font_size", "Title font size"),
            ("body_font_size", "Body font size"),
            ("star_count", "Star count"),
            ("crawl_speed", "Crawl speed"),
            ("crawl_width_chars", "Crawl line width (chars)"),
            ("crawl_depth", "Crawl depth"),
            ("horizon_y", "Horizon Y (0-1)"),
            ("bottom_margin", "Bottom margin (0-1)"),
            ("near_width_frac", "Near width fraction"),
            ("far_width_frac", "Far width fraction"),
            ("line_spacing", "Line spacing"),
            ("paragraph_spacing", "Paragraph spacing"),
            ("title_gap_scale", "Episode→Title gap scale"),
            ("section_gap_scale", "Title→Body gap scale"),
            ("crawl_body_align", "Crawl body alignment"),
            ("preview_scale", "Preview scale"),
            ("music_volume", "Music volume"),
            ("music_fade_in", "Music fade-in (s)"),
            ("music_fade_out", "Music fade-out (s)"),
            ("logo_zoom_start", "Logo zoom start"),
            ("logo_zoom_end", "Logo zoom end"),
            ("intro_color", "Intro color (#RRGGBB)"),
            ("crawl_color", "Crawl color (#RRGGBB)"),
            ("bg_color", "Background color (#RRGGBB)"),
            ("logo_bg_color", "Logo BG color (#RRGGBB)"),
        ]

        for key, label in fields:
            frm = ttk.Frame(settings_frame)
            frm.pack(fill="x", pady=4)

            ttk.Label(frm, text=label).pack(anchor="w")
            var = tk.StringVar()
            self.vars[key] = var

            if key == "crawl_body_align":
                combo = ttk.Combobox(frm, textvariable=var, values=["justify", "left", "center", "right"], state="readonly", width=10)
                combo.pack(anchor="w")
                combo.bind("<<ComboboxSelected>>", lambda e: self.schedule_preview())
            else:
                entry = ttk.Entry(frm, textvariable=var)
                entry.pack(fill="x")
                entry.bind("<KeyRelease>", lambda e: self.schedule_preview())
                entry.bind("<FocusOut>", lambda e: self.schedule_preview())

            if key in ("font_path", "logo_path", "music_path"):
                ttk.Button(frm, text="Browse", command=lambda k=key: self.browse_path(k)).pack(anchor="e", pady=(3, 0))

        tips = (
            "v4 improvements:\n"
            "- Crawl uses a true projective trapezoid warp.\n"
            "- Animated logo zoom before the crawl.\n"
            "- Music fades in and out during export.\n"
            "- Export section shows the save path directly.\n"
            "- If audio muxing fails, a *_silent.mp4 backup is preserved."
        )
        ttk.Label(settings_frame, text=tips, justify="left").pack(anchor="w", pady=10)

    def browse_path(self, key):
        if key == "font_path":
            path = filedialog.askopenfilename(title="Choose font file", filetypes=[("Font files", "*.ttf *.otf"), ("All files", "*.*")])
        elif key == "logo_path":
            path = filedialog.askopenfilename(title="Choose logo image", filetypes=[("Images", "*.png *.jpg *.jpeg *.webp *.bmp"), ("All files", "*.*")])
        elif key == "music_path":
            path = filedialog.askopenfilename(title="Choose background music", filetypes=[("Audio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"), ("All files", "*.*")])
        else:
            os.makedirs(_OUTPUT_DIR, exist_ok=True)
            path = filedialog.asksaveasfilename(title="Choose output MP4", initialdir=_OUTPUT_DIR, defaultextension=".mp4", filetypes=[("MP4 video", "*.mp4"), ("All files", "*.*")])
        if path:
            self.vars[key].set(path)
            self.schedule_preview()

    def _load_defaults(self):
        # Load from conf/conf.json if it exists, otherwise fall back to DEFAULTS
        cfg = dict(DEFAULTS)
        if os.path.exists(_DEFAULT_CONF):
            try:
                with open(_DEFAULT_CONF, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                cfg.update(loaded)
            except Exception:
                pass

        self.intro_text.delete("1.0", "end")
        self.intro_text.insert("1.0", cfg.get("intro_text", DEFAULTS["intro_text"]))
        self.intro_text.edit_modified(False)

        self.episode_text.delete("1.0", "end")
        self.episode_text.insert("1.0", cfg.get("episode_text", DEFAULTS["episode_text"]))
        self.episode_text.edit_modified(False)

        self.title_text.delete("1.0", "end")
        self.title_text.insert("1.0", cfg.get("title_text", DEFAULTS["title_text"]))
        self.title_text.edit_modified(False)

        self.crawl_text.delete("1.0", "end")
        self.crawl_text.insert("1.0", cfg.get("crawl_text", DEFAULTS["crawl_text"]))
        self.crawl_text.edit_modified(False)

        for k in self.vars:
            if k in cfg:
                self.vars[k].set(str(cfg[k]))

        self.update_preview_slider_range()

    def update_preview_slider_range(self):
        cfg = self.collect_config()
        total = cfg["duration_intro"] + cfg["duration_logo"] + cfg["duration_crawl"]
        self.preview_slider.configure(to=max(0.1, total))
        if self.preview_time_var.get() > total:
            self.preview_time_var.set(total)

    def on_text_modified(self, event=None):
        widget = event.widget if event else None
        if widget is not None:
            try:
                if widget.edit_modified():
                    widget.edit_modified(False)
            except Exception:
                pass
        self.schedule_preview()

    def collect_config(self):
        cfg = dict(DEFAULTS)

        cfg["intro_text"] = self.intro_text.get("1.0", "end").strip()
        cfg["episode_text"] = self.episode_text.get("1.0", "end").strip()
        cfg["title_text"] = self.title_text.get("1.0", "end").strip()
        cfg["crawl_text"] = self.crawl_text.get("1.0", "end").strip()

        for key in ["output_path", "font_path", "logo_path", "music_path", "intro_color", "crawl_color", "bg_color", "logo_bg_color", "crawl_body_align"]:
            if key in self.vars:
                cfg[key] = self.vars[key].get().strip() or DEFAULTS[key]
        if cfg["crawl_body_align"] not in ("justify", "left", "center", "right"):
            cfg["crawl_body_align"] = "justify"

        int_keys = ["width", "height", "fps", "intro_font_size", "episode_font_size", "title_font_size", "body_font_size", "star_count", "crawl_width_chars"]
        float_keys = [
            "duration_intro", "duration_logo", "duration_crawl",
            "crawl_speed", "crawl_depth",
            "horizon_y", "bottom_margin", "near_width_frac", "far_width_frac",
            "line_spacing", "paragraph_spacing", "preview_scale",
            "title_gap_scale", "section_gap_scale",
            "music_volume", "music_fade_in", "music_fade_out",
            "logo_zoom_start", "logo_zoom_end",
        ]

        for k in int_keys:
            cfg[k] = safe_int(self.vars[k].get(), DEFAULTS[k])
        for k in float_keys:
            cfg[k] = safe_float(self.vars[k].get(), DEFAULTS[k])

        cfg["width"] = max(320, cfg["width"])
        cfg["height"] = max(240, cfg["height"])
        cfg["fps"] = max(1, cfg["fps"])
        cfg["duration_intro"] = max(0.5, cfg["duration_intro"])
        cfg["duration_logo"] = max(0.0, cfg["duration_logo"])
        cfg["duration_crawl"] = max(1.0, cfg["duration_crawl"])
        cfg["star_count"] = max(50, cfg["star_count"])
        cfg["crawl_depth"] = max(0.2, cfg["crawl_depth"])
        cfg["crawl_width_chars"] = max(10, cfg["crawl_width_chars"])
        cfg["horizon_y"] = clamp(cfg["horizon_y"], 0.02, 0.6)
        cfg["bottom_margin"] = clamp(cfg["bottom_margin"], cfg["horizon_y"] + 0.1, 0.98)
        cfg["near_width_frac"] = clamp(cfg["near_width_frac"], 0.2, 1.2)
        cfg["far_width_frac"] = clamp(cfg["far_width_frac"], 0.01, 0.3)
        cfg["line_spacing"] = clamp(cfg["line_spacing"], 0.8, 2.0)
        cfg["paragraph_spacing"] = clamp(cfg["paragraph_spacing"], 0.0, 2.0)
        cfg["title_gap_scale"] = clamp(cfg["title_gap_scale"], 0.0, 5.0)
        cfg["section_gap_scale"] = clamp(cfg["section_gap_scale"], 0.0, 8.0)
        cfg["preview_scale"] = clamp(cfg["preview_scale"], 0.15, 0.95)
        cfg["music_volume"] = clamp(cfg["music_volume"], 0.0, 2.0)
        cfg["music_fade_in"] = clamp(cfg["music_fade_in"], 0.0, 20.0)
        cfg["music_fade_out"] = clamp(cfg["music_fade_out"], 0.0, 20.0)
        cfg["logo_zoom_start"] = clamp(cfg["logo_zoom_start"], 0.1, 5.0)
        cfg["logo_zoom_end"] = clamp(cfg["logo_zoom_end"], 0.1, 5.0)

        return cfg

    def save_project(self):
        cfg = self.collect_config()
        os.makedirs(_CONF_DIR, exist_ok=True)
        path = filedialog.asksaveasfilename(title="Save project JSON", initialdir=_CONF_DIR, defaultextension=".json", filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            self.status.config(text=f"Saved project: {path}")
        except Exception as e:
            messagebox.showerror("Save error", str(e))

    def load_project(self):
        path = filedialog.askopenfilename(title="Load project JSON", initialdir=_CONF_DIR, filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            self.intro_text.delete("1.0", "end")
            self.intro_text.insert("1.0", cfg.get("intro_text", DEFAULTS["intro_text"]))
            self.intro_text.edit_modified(False)

            self.episode_text.delete("1.0", "end")
            self.episode_text.insert("1.0", cfg.get("episode_text", DEFAULTS["episode_text"]))
            self.episode_text.edit_modified(False)

            self.title_text.delete("1.0", "end")
            self.title_text.insert("1.0", cfg.get("title_text", DEFAULTS["title_text"]))
            self.title_text.edit_modified(False)

            self.crawl_text.delete("1.0", "end")
            self.crawl_text.insert("1.0", cfg.get("crawl_text", DEFAULTS["crawl_text"]))
            self.crawl_text.edit_modified(False)

            for k in self.vars:
                self.vars[k].set(str(cfg.get(k, DEFAULTS.get(k, ""))))

            self.update_preview_slider_range()
            self.update_preview_now()
            self.status.config(text=f"Loaded project: {path}")
        except Exception as e:
            messagebox.showerror("Load error", str(e))

    def schedule_preview(self):
        self.update_preview_slider_range()
        if self.preview_after_id is not None:
            try:
                self.root.after_cancel(self.preview_after_id)
            except Exception:
                pass
        self.preview_after_id = self.root.after(250, self.update_preview_now)

    def update_preview_now(self):
        self.preview_after_id = None
        try:
            cfg = self.collect_config()
            t = float(self.preview_time_var.get())
            total = cfg["duration_intro"] + cfg["duration_logo"] + cfg["duration_crawl"]
            t = clamp(t, 0.0, total)
            self.preview_time_label.config(text=f"{t:.2f} s")

            img = make_preview_image(cfg, t)
            preview_w = max(200, int(cfg["width"] * cfg["preview_scale"]))
            preview_h = max(120, int(cfg["height"] * cfg["preview_scale"]))
            img = img.resize((preview_w, preview_h), Image.LANCZOS)

            tk_img = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=tk_img)
            self.preview_image_ref = tk_img
            self.status.config(text="Preview refreshed")
        except Exception as e:
            self.status.config(text=f"Preview error: {e}")

    def _set_export_buttons_state(self, state):
        for btn in (self._export_btn1, self._export_btn2):
            try:
                btn.config(state=state)
            except Exception:
                pass

    def start_render(self):
        if self.rendering:
            return

        cfg = self.collect_config()
        out_dir = os.path.dirname(os.path.abspath(cfg["output_path"]))
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Output error", f"Cannot create output folder:\n{out_dir}\n{e}")
            return

        self.rendering = True
        self.progress["value"] = 0
        self.status.config(text="Exporting...")
        self._set_export_buttons_state("disabled")

        def worker():
            try:
                def progress_cb(done, total, phase):
                    pct = 100.0 * done / max(total, 1)
                    self.root.after(0, lambda: self._update_progress(pct, done, total, phase))

                render_video(cfg, progress_cb=progress_cb)
                self.root.after(0, lambda: self._render_done(cfg["output_path"]))
            except Exception as e:
                self.root.after(0, lambda: self._render_error(str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _update_progress(self, pct, done, total, phase):
        self.progress["value"] = pct
        if phase == "audio":
            self.status.config(text="Exporting audio...")
        else:
            self.status.config(text=f"Exporting video... {done}/{total} frames ({pct:.1f}%)")

    def _render_done(self, path):
        self.rendering = False
        self.progress["value"] = 100
        self.status.config(text=f"Export complete: {path}")
        self._set_export_buttons_state("normal")
        messagebox.showinfo("Export complete", f"Video saved to:\n{path}")

    def _render_error(self, msg):
        self.rendering = False
        self.progress["value"] = 0
        self.status.config(text="Export failed")
        self._set_export_buttons_state("normal")
        messagebox.showerror("Export error", msg)

def main():
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    App(root)
    root.mainloop()

if __name__ == "__main__":
    main()
