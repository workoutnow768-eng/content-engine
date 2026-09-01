#!/usr/bin/env python3
"""Daily News lane: fetch today's headlines, render "10 things that happened
today" as a photo-backed carousel, and (optionally) schedule it via Buffer.

Two-phase, same pattern as the auto-post7 clips repo -- Buffer fetches images
from raw.githubusercontent.com, which only works AFTER the rendered slides are
committed and pushed:

  python engine/daily_news.py generate --client news [--count 10]
  python engine/daily_news.py schedule --client news

Photos per story (priority order, decided 2026-08-31):
  1. The story's own BBC RSS media:thumbnail (free, most relevant).
     NOTE: these are BBC/agency photos -- known takedown risk on a monetized
     channel, accepted by dez with AI fallback available if it becomes a problem.
  2. Higgsfield Soul (cloud API, costs cloud-pool credits) -- only for stories
     with no usable thumbnail, prompts avoid identifiable faces/text.
  3. Plain gradient card (never fails).

Exactly 10 slides = 10 stories; slide 1 doubles as the cover (extra header
badge). A separate cover would make 11 images, and Instagram's publishing API
caps carousels at 10.
"""
import argparse, datetime, html, io, json, os, re, sys, urllib.request
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "engine"))
from render import cover_fit, draw_wrapped, font_body, font_display, footer, hexc, badge, vertical_gradient, vignette  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

W, H = 1080, 1920
MEDIA_NS = "{http://search.yahoo.com/mrss/}"

FEEDS = [
    ("BBC", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC UK", "https://feeds.bbci.co.uk/news/uk/rss.xml"),
    ("BBC US", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
    ("BBC Tech", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
]

# Ranking (added 2026-09-01, dez's request): slides 1-3 should be the most
# "breaking" stories, biased toward what a US/UK audience cares about
# (e.g. US-Iran escalation belongs on slide 1). Keyword scoring -- crude but
# free, deterministic, and needs no API. Last 7 slides stay in feed order.
BREAKING_KW = {  # weight 3: hard-news escalation words
    "war": 3, "strike": 3, "attack": 3, "nuclear": 3, "missile": 3,
    "killed": 3, "dead": 3, "explosion": 3, "invasion": 3, "ceasefire": 3,
    "sanctions": 3, "hostage": 3, "shooting": 3, "crisis": 3, "emergency": 3,
    "breaking": 3, "assassin": 3, "coup": 3, "troops": 3, "airstrike": 3,
}
GEO_KW = {  # weight 2: US/UK relevance and major-power geopolitics
    "us ": 2, "u.s.": 2, "america": 2, "washington": 2, "white house": 2,
    "president": 2, "trump": 2, "congress": 2, "pentagon": 2,
    "uk ": 2, "britain": 2, "british": 2, "london": 2, "nhs": 2,
    "downing street": 2, "parliament": 2,
    "iran": 2, "china": 2, "russia": 2, "nato": 2, "israel": 2, "ukraine": 2,
    "election": 2, "economy": 2, "interest rate": 2, "inflation": 2,
}


def _score(item):
    text = " " + (item["title"] + " " + item["desc"]).lower() + " "
    s = 0
    for kw, w in BREAKING_KW.items():
        if kw in text:
            s += w
    for kw, w in GEO_KW.items():
        if kw in text:
            s += w
    return s


def rank_items(items, count):
    """Top 3 by breaking/US-UK score (highest first), then the rest of the
    slots filled in original feed order. Slide 1 = highest-scoring story."""
    scored = sorted(items, key=_score, reverse=True)
    top = scored[:3]
    rest = [it for it in items if it not in top]
    return (top + rest)[:count]

STYLE = {
    "bg_top": "#101010", "bg_bottom": "#050505",
    "text": "#F5F5F0", "text_dim": "#B9B9B0",
    "accent": "#FF3131", "badge_text": "#F5F5F0",
    "badge_hook": "TODAY'S NEWS", "swipe": "swipe for today's 10",
    "vignette": 150
}

STATE_PATH = os.path.join(ROOT, "state", "news_state.json")
POST_TIME_UTC = "07:30"  # workflow runs 06:00 UTC; post goes out 07:30 (08:30 UK summer)


def clean(t):
    t = html.unescape(re.sub(r"<[^>]+>", "", t or ""))
    return re.sub(r"\s+", " ", t).strip()


def _thumb_url(item_el):
    """Largest media:thumbnail / media:content URL on this RSS item, or None."""
    best, best_w = None, -1
    for tag in ("thumbnail", "content"):
        for el in item_el.iter(f"{MEDIA_NS}{tag}"):
            url = el.get("url")
            if not url:
                continue
            w = int(el.get("width", 0) or 0)
            if w > best_w:
                best, best_w = url, w
    return best


def fetch_items(count):
    items, seen = [], set()
    for source, url in FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            xml_data = urllib.request.urlopen(req, timeout=20).read()
            root = ET.fromstring(xml_data)
            for it in root.iter("item"):
                title = clean(it.findtext("title"))
                desc = clean(it.findtext("description"))
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                items.append({"source": source, "title": title,
                              "desc": desc[:160], "thumb": _thumb_url(it)})
        except Exception as e:
            print(f"WARN: feed {source} failed: {e}")
    return items[:count]


def _upsized_urls(url):
    """BBC serves tiny RSS thumbnails, but the size is baked into the URL
    (e.g. .../ace/standard/240/cpsprodpb/...). Requesting a bigger size
    returns the SAME photo at real resolution -- the 240px originals were
    the cause of the blurry slide backgrounds (fixed 2026-09-01). Yields
    candidate URLs, largest first, ending with the original."""
    for size in ("1600", "1024", "800"):
        bigger = re.sub(r"/(standard|branded_news|ws)/(\d{2,4})/", rf"/\1/{size}/", url)
        if bigger != url:
            yield bigger
    yield url


def download_photo(url, path):
    """Download a story photo at the highest available size; True on success."""
    for candidate in _upsized_urls(url):
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=20).read()
            img = Image.open(io.BytesIO(data)).convert("RGB")
            if img.width < 600 and candidate != url:
                continue  # try the next size down
            if img.width < 320:
                return False  # genuinely tiny -- let the AI/gradient fallback handle it
            img.save(path, quality=92)
            return True
        except Exception:
            continue
    print(f"WARN: no usable size for thumbnail {url[:80]}")
    return False


def _hf_prompt(item):
    """Illustrative, copyright/likeness-safe prompt for a headline."""
    return (
        f"photojournalistic editorial photograph illustrating this news topic: "
        f"{item['title']}. wide environmental shot, natural light, realistic, "
        f"no identifiable faces, no celebrities or politicians, no text, no logos, "
        f"no watermarks, moody documentary style"
    )


def generate_missing_photos(items, photo_paths):
    """Higgsfield Soul fallback for stories whose thumbnail failed. Only runs
    if HIGGSFIELD_API_KEY_ID is set; otherwise those slides stay gradient."""
    missing = [i for i, p in enumerate(photo_paths) if p is None]
    if not missing:
        return
    if not os.environ.get("HIGGSFIELD_API_KEY_ID"):
        print(f"INFO: {len(missing)} stories have no thumbnail and no Higgsfield "
              f"key is set -- rendering those on the gradient background.")
        return
    import higgsfield_client
    jobs = []
    for i in missing:
        out = photo_paths_target(photo_paths, i)
        jobs.append({"prompt": _hf_prompt(items[i]), "out_path": out})
    print(f"INFO: generating {len(jobs)} fallback images via Higgsfield "
          f"(cloud-pool credits will be charged)...")
    results, errors = higgsfield_client.generate_images_concurrent(jobs, max_workers=5)
    for j, i in enumerate(missing):
        if results[j] is not None:
            photo_paths[i] = results[j]
        else:
            print(f"WARN: fallback generation failed for story {i + 1}: {errors[j]} "
                  f"-- gradient background instead. (A per-slide failure is "
                  f"tolerable here, unlike auto-post7's all-or-nothing carousels.)")


def photo_paths_target(photo_paths, i):
    return photo_paths[i] if photo_paths[i] else _pending_paths[i]


_pending_paths = {}


def photo_canvas(path):
    """1080x1920 canvas from a story photo, darkened for text legibility."""
    img = cover_fit(Image.open(path).convert("RGB"))
    overlay = Image.new("RGB", (W, H), (0, 0, 0))
    img = Image.blend(img, overlay, 0.55)
    return vignette(img, strength=STYLE["vignette"])


def gradient_canvas():
    img = vertical_gradient(hexc(STYLE["bg_top"]), hexc(STYLE["bg_bottom"]))
    return vignette(img, strength=STYLE["vignette"])


def render_story(cfg, i, total, item, photo_path, date_str, path):
    img = photo_canvas(photo_path) if photo_path and os.path.exists(photo_path) else gradient_canvas()
    d = ImageDraw.Draw(img)
    if i == 1:  # slide 1 doubles as the cover
        badge(d, STYLE, f"{STYLE['badge_hook']} • {date_str}", 170)
        fc = font_display(52)
        d.text((W / 2 - d.textlength(f"{total} things that happened today", font=fc) / 2, 262),
               f"{total} things that happened today", font=fc, fill=hexc(STYLE["text"]))
    num = font_display(150)
    d.text((90, 400), f"{i:02d}", font=num, fill=hexc(STYLE["accent"]))
    fh = font_display(62)
    y = draw_wrapped(d, item["title"], fh, 90, 620, 900, hexc(STYLE["text"]))
    d.rectangle([90, y + 30, 290, y + 38], fill=hexc(STYLE["accent"]))
    if item["desc"]:
        fb = font_body(42)
        y = draw_wrapped(d, item["desc"], fb, 90, y + 90, 900, hexc(STYLE["text_dim"]), 1.3)
    fs = font_body(30)
    d.text((90, y + 40), "source: " + item["source"], font=fs, fill=hexc(STYLE["accent"]))
    if i == 1:
        d.text((90, y + 100), STYLE["swipe"] + " →", font=font_body(36), fill=hexc(STYLE["text"]))
    footer(d, STYLE, cfg.get("handle", "@yourbrand"))
    img.save(path, quality=92)


def load_cfg(client):
    cfg_path = os.path.join(ROOT, "clients", client, "config.json")
    return json.load(open(cfg_path, encoding="utf-8")) if os.path.exists(cfg_path) else {}


def outdir_for(client, today):
    return os.path.join(ROOT, "out", client, f"news_{today.isoformat()}")


def manifest_path(client):
    return os.path.join(ROOT, "out", client, "news_manifest.json")


def phase_generate(a):
    cfg = load_cfg(a.client)
    items = rank_items(fetch_items(40), a.count)  # big pool -> rank -> top N
    if len(items) < 5:
        sys.exit(f"only {len(items)} stories fetched -- refusing to post a thin carousel")
    today = datetime.date.today()
    outdir = outdir_for(a.client, today)
    os.makedirs(outdir, exist_ok=True)

    # 1. BBC thumbnails first (free), remember which stories still need a photo
    photo_paths = []
    for i, item in enumerate(items):
        p = os.path.join(outdir, f"photo{i + 1:02d}.jpg")
        _pending_paths[i] = p
        photo_paths.append(p if item["thumb"] and download_photo(item["thumb"], p) else None)
    got = sum(1 for p in photo_paths if p)
    print(f"INFO: {got}/{len(items)} story photos from BBC thumbnails")

    # 2. Higgsfield fallback only for the gaps
    generate_missing_photos(items, photo_paths)

    # 3. Render slides
    date_str = today.strftime("%d %b %Y")
    slide_repo_paths = []
    for i, item in enumerate(items, start=1):
        sp = os.path.join(outdir, f"slide{i:02d}.jpg")
        render_story(cfg, i, len(items), item, photo_paths[i - 1], date_str, sp)
        slide_repo_paths.append(os.path.relpath(sp, ROOT).replace(os.sep, "/"))

    cap = [f"{len(items)} things that happened today — {today.strftime('%d %B %Y')}."]
    cap += [f"{i}. {it['title']} ({it['source']})" for i, it in enumerate(items, 1)]
    cap.append("\nSources: BBC News RSS. Summaries condensed; full stories at bbc.co.uk/news")
    cap.append("#news #dailynews #newsfacts #today #worldnews")
    caption = "\n".join(cap)
    open(os.path.join(outdir, "caption.txt"), "w", encoding="utf-8").write(caption)

    hh, mm = [int(x) for x in POST_TIME_UTC.split(":")]
    post_at = datetime.datetime(today.year, today.month, today.day, hh, mm,
                                tzinfo=datetime.timezone.utc)
    if post_at < datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=20):
        post_at += datetime.timedelta(days=1)  # ran late -- don't hand Buffer a past time

    manifest = {
        "date": today.isoformat(),
        "caption": caption,
        "slides": slide_repo_paths,
        "scheduled_at": post_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(manifest_path(a.client), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"OK: rendered {len(items)} slides -> {outdir}; manifest written")


def phase_schedule(a):
    import buffer_client
    cfg = load_cfg(a.client)
    channels = cfg.get("buffer_channels", [])
    if not channels:
        sys.exit(f"clients/{a.client}/config.json has no buffer_channels -- nothing to schedule")
    with open(manifest_path(a.client)) as f:
        manifest = json.load(f)
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        sys.exit("GITHUB_REPOSITORY not set -- schedule phase only runs in GitHub Actions")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    urls = [f"https://raw.githubusercontent.com/{repo}/{branch}/{p}" for p in manifest["slides"]]

    ok = 0
    for ch in channels:
        try:
            buffer_client.create_post(ch, manifest["caption"], urls,
                                      manifest["scheduled_at"], "BUFFER_ACCESS_TOKEN_NEWS")
            print(f"OK: scheduled news carousel to {ch} for {manifest['scheduled_at']}")
            ok += 1
        except Exception as e:
            print(f"ERROR: {ch}: {e}")
    if ok == 0:
        sys.exit("all Buffer post attempts failed -- manifest left in place for retry")

    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    state = {"last_posted_date": manifest["date"],
             "last_run_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
             "scheduled_at": manifest["scheduled_at"]}
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
    os.remove(manifest_path(a.client))
    print(f"SUMMARY: {ok}/{len(channels)} channels scheduled")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", nargs="?", default="generate", choices=["generate", "schedule"])
    ap.add_argument("--client", default="news")
    ap.add_argument("--count", type=int, default=10)
    a = ap.parse_args()
    (phase_generate if a.phase == "generate" else phase_schedule)(a)


if __name__ == "__main__":
    main()
