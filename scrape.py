import json
import re
import html
import time
import os
import io
import math
import hashlib
import urllib.request
from datetime import datetime, timedelta, timezone
from PIL import Image

START = time.time()
FETCH_LIMIT = 9 * 60
WORK_LIMIT = 19 * 60
MAXP_A = 12

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

DEFAULT_BLOCK = [
    "stock", "in stock", "instock", "out of stock", "sold out", "restock",
    "available", "dispatch", "address", "shop no", "near", "road", "market",
    "floor", "pincode", "gst", "wholesale", "wholesaler", "wholeseller",
    "supplier", "manufacturer", "enterprise", "enterprises", "impex", "exim",
    "traders", "surat", "gujarat", "mumbai", "delhi", "contact", "whatsapp",
    "telegram", "join", "order", "booking", "cod", "shipping", "delivery",
    "moq", "minimum", "mrp", "rate", "price", "dm", "call",
]

GENERIC = [
    "wholesale", "wholesaler", "wholeseller", "wholsale", "enterprises",
    "enterprise", "ecommerce", "ecommerse", "import", "export", "impex",
    "exim", "store", "mart", "shop", "zone", "hub", "surat", "bazaar",
    "bazar", "traders", "trading", "retail", "online", "official", "global",
    "world", "sales",
]

BAD = re.compile(
    r"(₹|\brs\.?\b|\binr\b|/-|t\.me|https?://|www\.|@\w+|\+?\d[\d\s-]{8,}\d)",
    re.I,
)


def elapsed():
    return time.time() - START


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_page(h, before=None):
    url = "https://t.me/s/" + h
    if before:
        url += "?before=%d" % before
    try:
        return get(url).decode("utf-8", "ignore")
    except Exception as e:
        print(h, "fetch error:", e)
        return None


def load_settings():
    s = {"slabs": DEFAULT_SLABS, "days": 30, "whatsapp": "",
         "shop_name": "Shop", "order_url": "", "block_words": []}
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


def handle_cores(handles):
    cores = set()
    for h in handles:
        full = re.sub(r"[^a-z]", "", h.lower())
        s = full
        for g in GENERIC:
            s = s.replace(g, "")
        if len(s) >= 6:
            cores.add(s)
        if len(full) >= 6:
            cores.add(full)
    return cores


def clean_text(text, cores, block_re):
    good = []
    for line in text.split("\n"):
        l = line.strip()
        if not l:
            continue
        low = l.lower()
        flat = re.sub(r"[^a-z]", "", low)
        if BAD.search(l) or block_re.search(low):
            continue
        if any(c in flat for c in cores):
            continue
        good.append(l)
    return "\n".join(good)[:500]


def find_price(text):
    found = []
    pats = [
        r"(?:₹|rs\.?|inr)\s*:?\s*(\d{2,6})",
        r"(\d{2,6})\s*(?:/-|₹|rs\b)",
        r"@\s*(\d{2,5})\b",
        r"(?:price|rate)\s*[:=\-]?\s*(\d{2,6})",
    ]
    for p in pats:
        for m in re.finditer(p, text, re.I):
            v = int(m.group(1))
            if 20 <= v <= 100000:
                found.append(v)
    return min(found) if found else None


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
        return bits
    except Exception:
        return None


def dist(a, b):
    return bin(a ^ b).count("1")


def parse_date(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def parse_page(h, page):
    nums = [int(x) for x in re.findall(r'data-post="[^"/]+/(\d+)"', page)]
    dates = [parse_date(x) for x in
             re.findall(r'<time[^>]*datetime="([^"]+)"', page)]
    dates = [d for d in dates if d]
    items = []
    for b in page.split("tgme_widget_message_wrap")[1:]:
        m = re.search(r'data-post="[^"/]+/(\d+)"', b)
        if not m:
            continue
        num = int(m.group(1))
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
            "id": "%s/%d" % (h, num), "num": num, "channel": h,
            "text": text, "images": images,
            "video": videos[0] if videos else "", "date": date,
        })
    return {"items": items,
            "min": min(nums) if nums else None,
            "max": max(nums) if nums else None,
            "oldest": min(dates) if dates else None}


def close(a, b):
    da, db = parse_date(a), parse_date(b)
    return bool(da and db and abs((da - db).total_seconds()) <= 120)


def group(items, h):
    uniq = {}
    for it in items:
        uniq[it["num"]] = it
    out = []
    for it in sorted(uniq.values(), key=lambda x: x["num"]):
        g = out[-1] if out else None
        if (g and it["num"] == g["last"] + 1 and close(g["date"], it["date"])
                and (not it["text"] or not g["text"])):
            for u in it["images"]:
                if u not in g["images"]:
                    g["images"].append(u)
            g["posts"].append(it["id"])
            g["last"] = it["num"]
            if not g["text"]:
                g["text"] = it["text"]
            if not g["video"]:
                g["video"] = it["video"]
            if it["date"] > g["date"]:
                g["date"] = it["date"]
        else:
            out.append({
                "channel": h, "posts": [it["id"]], "last": it["num"],
                "text": it["text"], "images": list(it["images"]),
                "video": it["video"], "date": it["date"],
            })
    for g in out:
        g["images"] = g["images"][:8]
    return out


def make_id(pid):
    return "p" + hashlib.sha1(pid.encode()).hexdigest()[:8]


def main():
    st = load_settings()
    limit = datetime.now(timezone.utc) - timedelta(days=int(st["days"]))
    os.makedirs("site", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    handles = []
    for line in open("channels.txt"):
        h = line.strip()
        if h and h.lower() not in [x.lower() for x in handles]:
            handles.append(h)
    hset = {h.lower() for h in handles}
    cores = handle_cores(handles)
    words = DEFAULT_BLOCK + [w.lower() for w in st.get("block_words", []) if w]
    block_re = re.compile(
        r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z0-9])")

    products, skipped, state, pending = [], {}, {}, []
    if os.path.exists(RAW):
        try:
            raw = json.load(open(RAW, encoding="utf-8"))
            if raw.get("version") == 2:
                products = raw["products"]
                skipped = dict.fromkeys(raw.get("skipped", []))
                state = raw.get("state", {})
                pending = raw.get("pending", [])
        except Exception as e:
            print("raw.json problem:", e)

    pending = [g for g in pending if g["channel"].lower() in hset]
    for p in products:
        p["sources"] = {c: s for c, s in p["sources"].items() if c.lower() in hset}
    products = [p for p in products if p["sources"]]

    seen = set(skipped.keys())
    for p in products:
        seen.update(p["posts"])
    for g in pending:
        seen.update(g["posts"])

    fetched = {h: [] for h in handles}
    failed = []

    for h in handles:
        s = state.setdefault(h, {"newest": 0, "cursor": None, "done": False})
        before, top, lowest = None, None, None
        for _ in range(MAXP_A):
            if elapsed() > FETCH_LIMIT:
                break
            page = fetch_page(h, before)
            if page is None:
                failed.append(h)
                break
            r = parse_page(h, page)
            if r["min"] is None:
                if before is None:
                    print(h, "has no public posts")
                s["done"] = True
                break
            fetched[h] += r["items"]
            top = r["max"] if top is None else max(top, r["max"])
            lowest = r["min"] if lowest is None else min(lowest, r["min"])
            before = r["min"]
            if r["min"] <= s["newest"]:
                break
            if r["oldest"] and r["oldest"] < limit:
                if s["cursor"] is None:
                    s["done"] = True
                break
            time.sleep(0.3)
        if top:
            s["newest"] = max(s["newest"], top)
        if s["cursor"] is None and lowest is not None:
            s["cursor"] = lowest
        print(h, "-> new posts fetched:", len(fetched[h]))

    fails = {}
    while elapsed() < FETCH_LIMIT:
        todo = [h for h in handles
                if not state[h]["done"] and state[h]["cursor"]
                and fails.get(h, 0) < 3]
        if not todo:
            break
        for h in todo:
            if elapsed() >= FETCH_LIMIT:
                break
            s = state[h]
            page = fetch_page(h, s["cursor"])
            if page is None:
                fails[h] = fails.get(h, 0) + 1
                continue
            r = parse_page(h, page)
            if r["min"] is None:
                s["done"] = True
                continue
            fetched[h] += r["items"]
            s["cursor"] = r["min"]
            if (r["oldest"] and r["oldest"] < limit) or r["min"] <= 1:
                s["done"] = True
            time.sleep(0.3)

    for h, items in fetched.items():
        for g in group(items, h):
            if all(pp in seen for pp in g["posts"]):
                continue
            pending.append(g)
            seen.update(g["posts"])

    pending.sort(key=lambda g: g["date"], reverse=True)
    rest = []
    new_count = upd_count = 0
    for i, g in enumerate(pending):
        if elapsed() > WORK_LIMIT:
            rest = pending[i:]
            break
        d = parse_date(g["date"])
        price = find_price(g["text"])
        if not d or d < limit or price is None or not g["images"]:
            for pp in g["posts"]:
                skipped[pp] = None
            continue
        ctext = clean_text(g["text"], cores, block_re)
        h = dhash(g["images"][0])
        key = title_key(ctext)
        src = {"price": price, "date": g["date"], "post": g["posts"][0]}
        match = None
        for p in products:
            if h is not None and any(dist(h, x) <= 10 for x in p["hashes"]):
                match = p
                break
            if key and key == p.get("key"):
                match = p
                break
        if match:
            old = match["sources"].get(g["channel"])
            if not old or parse_date(g["date"]) >= parse_date(old["date"]):
                match["sources"][g["channel"]] = src
            match["posts"] += g["posts"]
            if h is not None and h not in match["hashes"]:
                match["hashes"] = (match["hashes"] + [h])[-4:]
            if len(g["images"]) > len(match["images"]):
                match["images"] = g["images"]
            if g["video"] and not match["video"]:
                match["video"] = g["video"]
            if len(g["text"]) > len(match["raw"]):
                match["raw"] = g["text"][:1500]
            upd_count += 1
        else:
            products.append({
                "pid": g["posts"][0], "hashes": [h] if h is not None else [],
                "key": key, "raw": g["text"][:1500],
                "images": g["images"], "video": g["video"],
                "sources": {g["channel"]: src}, "posts": list(g["posts"]),
            })
            new_count += 1
    pending = rest

    for p in products:
        p["sources"] = {c: s for c, s in p["sources"].items()
                        if parse_date(s["date"]) and parse_date(s["date"]) >= limit}
    products = [p for p in products if p["sources"]]

    out = []
    hidden = 0
    for p in products:
        latest = max(p["sources"].values(), key=lambda s: parse_date(s["date"]))
        sell = sell_price(latest["price"], st["slabs"])
        if sell is None:
            hidden += 1
            continue
        text = clean_text(p["raw"], cores, block_re)
        title = text.split("\n")[0][:70] if text else "New Arrival"
        out.append({
            "id": make_id(p["pid"]), "title": title, "text": text,
            "images": p["images"], "video": p["video"], "price": sell,
            "date": latest["date"],
        })
    out.sort(key=lambda x: x["date"], reverse=True)

    json.dump({"version": 2, "products": products,
               "skipped": list(skipped.keys())[-20000:],
               "state": state, "pending": pending},
              open(RAW, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"whatsapp": st["whatsapp"], "shop_name": st["shop_name"],
               "order_url": st["order_url"]},
              open(CONF, "w", encoding="utf-8"), ensure_ascii=False)

    backlog = len([h for h in handles if not state[h]["done"]])
    print("DONE. new:", new_count, "merged-duplicates:", upd_count,
          "live:", len(out), "hidden(>2000):", hidden,
          "| waiting to process:", len(pending),
          "| channels still backfilling:", backlog,
          "| failed:", len(failed),
          "| minutes used:", round(elapsed() / 60, 1))
    if pending or backlog:
        print("PARTIAL: run again or wait for the next daily run to finish the rest")


main()
