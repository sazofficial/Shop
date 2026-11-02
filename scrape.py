import json
import re
import html
import time
import os
import io
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from PIL import Image

RAW = "data/raw.json"
OUT = "site/products.json"
CONF = "site/config.json"

DEFAULT_SLABS = [
    {"upto": 100, "add": 50},
    {"upto": 200, "add": 80},
    {"upto": 500, "add": 150},
    {"upto": 1000, "add": 400},
    {"upto": 2000, "add": 700},
]


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def fetch(handle):
    return get("https://t.me/s/" + handle).decode("utf-8", "ignore")


def load_settings():
    s = {"slabs": DEFAULT_SLABS, "days": 30,
         "whatsapp": "", "shop_name": "Shop"}
    try:
        s.update(json.load(open("settings.json", encoding="utf-8")))
    except Exception as e:
        print("settings.json problem:", e)
    return s


def sell_price(cost, slabs):
    for sl in slabs:
        if cost <= sl["upto"]:
            return int(math.ceil((cost + sl["add"]) / 10.0) * 10)
    return None


def clean(t):
    t = re.sub(r"<br\s*/?>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


BAD = re.compile(
    r"(₹|\brs\.?\b|\binr\b|price|rate\s*[:=]|/-|moq|wholesale|whatsapp|"
    r"contact|call us|call now|order now|bulk order|booking|join|t\.me|"
    r"https?://|www\.|@\w+|\+?\d[\d\s-]{8,}\d)",
    re.I,
)


def clean_text(text):
    lines = [l.strip() for l in text.split("\n")]
    good = [l for l in lines if l and not BAD.search(l)]
    return "\n".join(good)[:600]


def find_price(text):
    m = re.search(r"(?:₹|rs\.?|inr)\s*:?\s*(\d{2,6})", text, re.I)
    if not m:
        m = re.search(r"(\d{2,6})\s*(?:/-|₹|rs\b)", text, re.I)
    return int(m.group(1)) if m else None


def title_key(text):
    for line in text.split("\n"):
        line = line.strip()
        if line:
            k = re.sub(r"[^a-z0-9]", "", line.lower())
            return k if len(k) >= 15 else ""
    return ""


def dhash(url):
    try:
        im = Image.open(io.BytesIO(get(url))).convert("L").resize((9, 8))
        px = list(im.getdata())
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
        return format(bits, "x")
    except Exception:
        return None


def dist(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def parse_date(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def parse(handle, page):
    items = []
    for b in page.split("tgme_widget_message_wrap")[1:]:
        m = re.search(r'data-post="([^"]+)"', b)
        if not m:
            continue
        post = m.group(1)
        images = re.findall(
            r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", b)
        videos = re.findall(r'<video[^>]*src="([^"]+)"', b)
        thumbs = re.findall(
            r"tgme_widget_message_video_thumb[^>]*background-image:url\('([^']+)'\)", b)
        tm = re.search(
            r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', b, re.S)
        text = clean(tm.group(1)) if tm else ""
        dm = re.search(r'<time[^>]*datetime="([^"]+)"', b)
        date = dm.group(1) if dm else ""
        if not images and thumbs:
            images = thumbs
        if not images:
            continue
        items.append({
            "id": post, "channel": handle, "text": text,
            "price": find_price(text), "images": images,
            "video": videos[0] if videos else "",
            "date": date, "link": "https://t.me/" + post,
        })
    return items


def main():
    st = load_settings()
    limit = datetime.now(timezone.utc) - timedelta(days=int(st["days"]))
    os.makedirs("site", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    products, skipped = [], set()
    if os.path.exists(RAW):
        try:
            raw = json.load(open(RAW, encoding="utf-8"))
            products = raw.get("products", [])
            skipped = set(raw.get("skipped", []))
        except Exception:
            pass
    seen = set()
    for p in products:
        seen.update(p.get("posts", []))

    handles = []
    for line in open("channels.txt"):
        h = line.strip()
        if h and h not in handles:
            handles.append(h)

    posts, failed = [], []
    for h in handles:
        try:
            got = parse(h, fetch(h))
            posts.extend(got)
            print(h, "->", len(got), "posts")
        except Exception as e:
            print(h, "FAILED:", e)
            failed.append(h)
        time.sleep(1)

    posts.sort(key=lambda x: x["date"])
    new_count = upd_count = 0
    for it in posts:
        if it["id"] in seen or it["id"] in skipped:
            continue
        d = parse_date(it["date"])
        if not d or d < limit or it["price"] is None:
            skipped.add(it["id"])
            continue
        ctext = clean_text(it["text"])
        h = dhash(it["images"][0])
        key = title_key(ctext)
        src = {"price": it["price"], "date": it["date"],
               "post": it["id"], "link": it["link"]}
        match = None
        for p in products:
            if h and p.get("hash") and dist(h, p["hash"]) <= 8:
                match = p
                break
            if key and key == p.get("key"):
                match = p
                break
        if match:
            old = match["sources"].get(it["channel"])
            if not old or parse_date(it["date"]) >= parse_date(old["date"]):
                match["sources"][it["channel"]] = src
            match["posts"].append(it["id"])
            upd_count += 1
        else:
            title = ctext.split("\n")[0][:80] if ctext else "Product"
            products.append({
                "pid": it["id"].replace("/", "_"),
                "hash": h, "key": key, "title": title, "text": ctext,
                "images": it["images"][:5], "video": it["video"],
                "sources": {it["channel"]: src}, "posts": [it["id"]],
            })
            new_count += 1

    for p in products:
        p["sources"] = {c: s for c, s in p["sources"].items()
                        if parse_date(s["date"]) and parse_date(s["date"]) >= limit}
    products = [p for p in products if p["sources"]]

    out = []
    hidden = 0
    for p in products:
        cost = max(s["price"] for s in p["sources"].values())
        sell = sell_price(cost, st["slabs"])
        if sell is None:
            hidden += 1
            continue
        out.append({
            "id": p["pid"], "title": p["title"], "text": p["text"],
            "images": p["images"], "video": p["video"], "price": sell,
            "date": max(s["date"] for s in p["sources"].values()),
        })
    out.sort(key=lambda x: x["date"], reverse=True)

    json.dump({"products": products, "skipped": sorted(skipped)[-5000:]},
              open(RAW, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"whatsapp": st["whatsapp"], "shop_name": st["shop_name"]},
              open(CONF, "w", encoding="utf-8"), ensure_ascii=False)
    print("DONE. new:", new_count, "price-updates:", upd_count,
          "live:", len(out), "hidden(>2000):", hidden,
          "failed channels:", len(failed))
    if failed:
        print("Failed:", ", ".join(failed))


main()
