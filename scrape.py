import json
import re
import html
import time
import os
import urllib.request

OUT = "site/products.json"
MAX_ITEMS = 3000


def fetch(handle):
    req = urllib.request.Request(
        "https://t.me/s/" + handle,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "ignore")


def clean(t):
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def find_price(text):
    m = re.search(r"(?:₹|rs\.?|inr)\s*:?\s*(\d{2,6})", text, re.I)
    if not m:
        m = re.search(r"(\d{2,6})\s*(?:/-|₹|rs\b)", text, re.I)
    return int(m.group(1)) if m else None


def parse(handle, page):
    items = []
    blocks = page.split("tgme_widget_message_wrap")[1:]
    for b in blocks:
        m = re.search(r'data-post="([^"]+)"', b)
        if not m:
            continue
        post = m.group(1)
        images = re.findall(
            r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", b
        )
        videos = re.findall(r'<video[^>]*src="([^"]+)"', b)
        thumbs = re.findall(
            r"tgme_widget_message_video_thumb[^>]*background-image:url\('([^']+)'\)", b
        )
        tm = re.search(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S
        )
        text = clean(tm.group(1)) if tm else ""
        dm = re.search(r'<time[^>]*datetime="([^"]+)"', b)
        date = dm.group(1) if dm else ""
        if not images and not videos and not thumbs:
            continue
        if not images and thumbs:
            images = thumbs
        items.append(
            {
                "id": post,
                "channel": handle,
                "text": text,
                "price": find_price(text),
                "images": images,
                "video": videos[0] if videos else "",
                "date": date,
                "link": "https://t.me/" + post,
            }
        )
    return items


def main():
    os.makedirs("site", exist_ok=True)
    old = {}
    if os.path.exists(OUT):
        try:
            for p in json.load(open(OUT, encoding="utf-8")):
                old[p["id"]] = p
        except Exception:
            pass

    with open("channels.txt") as f:
        handles = []
        for line in f:
            h = line.strip()
            if h and h not in handles:
                handles.append(h)

    ok = 0
    failed = []
    for h in handles:
        try:
            items = parse(h, fetch(h))
            for it in items:
                old[it["id"]] = it
            print(h, "->", len(items), "products")
            ok += 1
        except Exception as e:
            print(h, "FAILED:", e)
            failed.append(h)
        time.sleep(1)

    products = sorted(old.values(), key=lambda p: p["date"], reverse=True)[:MAX_ITEMS]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(products, f, ensure_ascii=False)
    print("DONE. channels ok:", ok, "failed:", len(failed), "total products:", len(products))
    if failed:
        print("Failed channels:", ", ".join(failed))


main()
