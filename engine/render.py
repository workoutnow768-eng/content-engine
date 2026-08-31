#!/usr/bin/env python3
"""Content Engine renderer: niche pack JSON -> 4-slide 1080x1920 carousel.

Usage:
  python engine/render.py --niche creepy_history --entry 0 --client EXAMPLE
  python engine/render.py --niche tech_facts --all --client EXAMPLE
"""
import argparse, json, os, sys, textwrap

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1080, 1920


def load_font(names, size):
    from PIL import ImageFont
    search = [os.path.join(ROOT, "engine", "fonts"),
              "/usr/share/fonts/truetype/msttcorefonts",
              "/usr/share/fonts/truetype/montserrat",
              "/usr/share/fonts/truetype/dejavu",
              "/usr/share/fonts", "C:/Windows/Fonts"]
    for n in names:
        for d in search:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    for d in search:
        if os.path.isdir(d):
            for r, _, fs in os.walk(d):
                for f in fs:
                    if f.lower().endswith(".ttf"):
                        return ImageFont.truetype(os.path.join(r, f), size)
    return ImageFont.load_default()


def font_display(size):
    return load_font(["Montserrat-ExtraBold.ttf", "Montserrat-Black.ttf",
                      "DejaVuSans-Bold.ttf", "arialbd.ttf"], size)


def font_body(size):
    return load_font(["Montserrat-Medium.ttf", "Montserrat-Regular.ttf",
                      "DejaVuSans.ttf", "arial.ttf"], size)


def hexc(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def vertical_gradient(top, bottom):
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(W):
            px[x, y] = c
    return img


def vignette(img, strength=140):
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([-W * 0.35, -H * 0.25, W * 1.35, H * 1.25], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(220))
    black = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(img, black, mask.point(lambda v: 255 - int((255 - v) * strength / 255)))


def wrap_to_width(draw, text, font, max_w):
    lines = []
    for para in text.split("\n"):
        words, cur = para.split(), ""
        for w in words:
            trial = (cur + " " + w).strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def draw_wrapped(draw, text, font, x, y, max_w, fill, line_gap=1.12, anchor_center=False):
    lines = wrap_to_width(draw, text, font, max_w)
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * line_gap)
    for i, line in enumerate(lines):
        lx = x
        if anchor_center:
            lx = x - draw.textlength(line, font=font) / 2
        draw.text((lx, y + i * lh), line, font=font, fill=fill)
    return y + len(lines) * lh


def cover_fit(img):
    sw, sh = img.size
    scale = max(W / sw, H / sh)
    img = img.resize((int(sw * scale) + 1, int(sh * scale) + 1), Image.LANCZOS)
    x = (img.width - W) // 2
    y = (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def base_canvas(style):
    """Gradient canvas, or a photo background (style key `bg_image`, a file in
    niches/backgrounds/) darkened for text legibility. Photo missing on disk
    falls back to the gradient so renders never fail."""
    bg = style.get("bg_image")
    if bg:
        path = os.path.join(ROOT, "niches", "backgrounds", bg)
        if os.path.exists(path):
            img = cover_fit(Image.open(path).convert("RGB"))
            dark = int(255 * style.get("bg_darken", 0.45))
            overlay = Image.new("RGB", (W, H), (0, 0, 0))
            img = Image.blend(img, overlay, dark / 255)
            return vignette(img, strength=style.get("vignette", 130))
    img = vertical_gradient(hexc(style["bg_top"]), hexc(style["bg_bottom"]))
    img = vignette(img, strength=style.get("vignette", 130))
    return img


def footer(draw, style, handle):
    f = font_body(34)
    txt = handle
    tw = draw.textlength(txt, font=f)
    draw.rectangle([W / 2 - tw / 2 - 26, H - 150, W / 2 + tw / 2 + 26, H - 86],
                   outline=hexc(style["accent"]), width=2)
    draw.text((W / 2 - tw / 2, H - 138), txt, font=f, fill=hexc(style["text_dim"]))


def badge(draw, style, label, y):
    f = font_display(30)
    tw = draw.textlength(label, font=f)
    x0 = W / 2 - tw / 2 - 28
    draw.rectangle([x0, y, x0 + tw + 56, y + 62], fill=hexc(style["accent"]))
    draw.text((x0 + 28, y + 12), label, font=f, fill=hexc(style["badge_text"]))


def render_hook(style, entry, handle, path):
    img = base_canvas(style)
    d = ImageDraw.Draw(img)
    badge(d, style, style["badge_hook"], 430)
    f = font_display(92)
    d_wrapped_y = draw_wrapped(d, entry["hook"], f, W / 2, 620, 900,
                               hexc(style["text"]), anchor_center=True)
    fs = font_body(40)
    sub = style.get("swipe", "swipe for the facts")
    d.text((W / 2 - d.textlength(sub, font=fs) / 2, d_wrapped_y + 60), sub,
           font=fs, fill=hexc(style["accent"]))
    footer(d, style, handle)
    img.save(path, quality=92)


def render_fact(style, entry, i, handle, path):
    img = base_canvas(style)
    d = ImageDraw.Draw(img)
    fact = entry["facts"][i]
    num = font_display(150)
    d.text((90, 300), f"{i + 1:02d}", font=num, fill=hexc(style["accent"]))
    fh = font_display(66)
    y = draw_wrapped(d, fact["h"], fh, 90, 520, 900, hexc(style["text"]))
    d.rectangle([90, y + 34, 290, y + 42], fill=hexc(style["accent"]))
    fb = font_body(44)
    draw_wrapped(d, fact["b"], fb, 90, y + 96, 900, hexc(style["text_dim"]), 1.3)
    footer(d, style, handle)
    img.save(path, quality=92)


def render_entry(niche, idx, client_cfg, outdir):
    entry = niche["entries"][idx]
    style = dict(niche["style"])
    if entry.get("bg"):
        style["bg_image"] = entry["bg"]
    if client_cfg.get("accent_override"):
        style["accent"] = client_cfg["accent_override"]
    handle = client_cfg.get("handle", "@yourbrand")
    os.makedirs(outdir, exist_ok=True)
    render_hook(style, entry, handle, os.path.join(outdir, "slide1.jpg"))
    for i in range(3):
        render_fact(style, entry, i, handle, os.path.join(outdir, f"slide{i + 2}.jpg"))
    with open(os.path.join(outdir, "caption.txt"), "w", encoding="utf-8") as f:
        f.write(entry["caption"] + "\n\n" + " ".join(entry["hashtags"]))
    print(f"rendered [{niche['name']}] entry {idx}: {entry['hook'][:40]}... -> {outdir}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--niche", required=True)
    ap.add_argument("--entry", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--client", default="EXAMPLE")
    a = ap.parse_args()
    with open(os.path.join(ROOT, "niches", a.niche + ".json"), encoding="utf-8") as f:
        niche = json.load(f)
    ccfg_path = os.path.join(ROOT, "clients", a.client, "config.json")
    ccfg = {}
    if os.path.exists(ccfg_path):
        ccfg = json.load(open(ccfg_path, encoding="utf-8"))
    idxs = range(len(niche["entries"])) if a.all else [a.entry or 0]
    for i in idxs:
        outdir = os.path.join(ROOT, "out", a.client, f"{a.niche}_{i:03d}")
        render_entry(niche, i, ccfg, outdir)


if __name__ == "__main__":
    sys.exit(main())
