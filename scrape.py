import json
import re
import html
import time
import os
import io
import math
import hashlib
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from PIL import Image

START = time.time()
FETCH_LIMIT = 8 * 60
WORK_LIMIT = 16 * 60
MAXP_A = 12
BATCH = 24
RECHECK = 120
VERSION = 5

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

COMMON = {
    "fashion", "ladies", "gents", "kids", "mens", "women", "womens", "fancy",
    "collection", "premium", "quality", "products", "product", "items",
    "mobile", "accessories", "electronics", "garments", "home", "kitchen",
    "baby", "toys", "gift", "gifts", "general", "gadgets", "watches",
    "jewellery", "jewelry", "bags", "shoes", "wear", "style", "trendy",
    "new", "latest", "best", "super", "business", "market", "deals", "deal",
    "mega", "smart", "king", "city", "india", "indian", "digital",
}

BAD = re.compile(
    r"(₹|\brs\.?\b|\binr\b|/-|t\.me|https?://|www\.|@\w+|\+?\d[\d\s.\-()]{8,}\d)",
    re.I,
)

SECRET = re.compile(
    r"(AKIA[0-9A-Z]{16}|AKLT[0-9A-Za-z]{16,}|ASIA[0-9A-Z]{16}|"
    r"ghp_[0-9A-Za-z]{36}|gho_[0-9A-Za-z]{36}|ghs_[0-9A-Za-z]{36}|"
    r"ghu_[0-9A-Za-z]{36}|github_pat_[0-9A-Za-z_]{22,}|"
    r"sk_live_[0-9A-Za-z]{24,}|xox[bpas]-[0-9A-Za-z-]{10,}|"
    r"AIza[0-9A-Za-z\-_]{35})"
)

CODE_PREFIX = re.compile(r"^\s*[A-Za-z]{2,4}[\s\-_:#|]*\d{1,5}\s*[\-_:|]?\s*")


def redact(t):
    if not t:
        return t
    return SECRET.sub("[hidden]", t)


def elapsed():
    return time.time() - START


def get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_page(h, before=None):
    url = "https://t.me/s/" + h
    if before:
        url += "?before=%d" % before
    try:
        return get(url, 15).decode("utf-8", "ignore")
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


def line_flat(l):
    return re.sub(r"[^a-z0-9]", "", l.lower())


def build_boiler(corpus):
    out = {}
    for ch, texts in corpus.items():
        n = len(texts)
        cnt = {}
        for t in texts:
            lines = [l.strip() for l in t.split("\n") if l.strip()]
            if not lines:
                continue
            for l in {lines[0], lines[-1]}:
                k = line_flat(l)
                if len(k) >= 6:
                    cnt[k] = cnt.get(k, 0) + 1
        out[ch] = {k for k, c in cnt.items() if c >= 3 and c >= 0.1 * n}
    return out


def build_ctx(handles, state, corpus, block_re, cores):
    names_full, names_words = set(), set()
    for h in handles:
        low = state.get(h, {}).get("title", "").lower()
        full = re.sub(r"[^a-z0-9]", "", low)
        if len(full) >= 7:
            names_full.add(full)
        for w in re.findall(r"[a-z]+", low):
            if len(w) >= 5 and w not in GENERIC and w not in COMMON:
                names_words.add(w)
    boiler = build_boiler(corpus)
    return {"cores": cores, "block_re": block_re, "names_full": names_full,
            "names_words": names_words, "boiler": boiler}


def clean_text(text, ch, ctx):
    good = []
    boil = ctx["boiler"].get(ch, set())
    for line in text.split("\n"):
        l = line.strip()
        if not l:
            continue
        low = l.lower()
        flat = line_flat(l)
        if BAD.search(l) or ctx["block_re"].search(low):
            continue
        if flat in boil:
            continue
        if any(c in flat for c in ctx["cores"]):
            continue
        if any(n in flat for n in ctx["names_full"]):
            continue
        if set(re.findall(r"[a-z]+", low)) & ctx["names_words"]:
            continue
        good.append(l)
    return "\n".join(good)[:500]


def find_price(text):
    def _valid(m):
        before = text[max(0, m.start() - 20):m.start()].lower()
        after = text[m.end():m.end() + 12]
        if re.search(r"(?:mrp|m\.r\.p|moq|qty|minimum|set\s+of|pack\s+of|"
                     r"box\s+of|dozen|worth|upto|up\s*to)", before):
            return False
        if re.match(r"\s*(?:pcs|pc|piece|pieces|pis|mtr|meter|metre|kg|gm|"
                    r"ml|set|pack|inch|cm|mm|ft)\b", after, re.I):
            return False
        return True

    def _collect(pats):
        vals = []
        for p in pats:
            for m in re.finditer(p, text, re.I):
                v = int(m.group(1))
                if 20 <= v <= 100000 and _valid(m):
                    vals.append(v)
        return vals

    for tier in (
        [
            r"(?:₹|rs\.?|inr)\s*:?\s*(\d{2,6})",
            r"(\d{2,6})\s*(?:/-|₹|rs\b|rupees?\b)",
        ],
        [
            r"(?:price|rate|selling|our\s*price)\s*[:=\-]?\s*(?:₹|rs\.?)?\s*(\d{2,6})",
            r"\b(\d{2,6})\s*(?:each|per\s*(?:pc|piece|pcs?|kg|mtr))\b",
        ],
        [
            r"@\s*(\d{2,5})\b",
        ],
    ):
        vals = _collect(tier)
        if vals:
            return min(vals)
    return None


def clean_name(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return "New Arrival"
    for line in lines:
        stripped = CODE_PREFIX.sub("", line).strip()
        if len(stripped) >= 3:
            return stripped[:70]
    return lines[0][:70]


def norm_unit(u):
    u = u.lower()
    if u == "m" or u.startswith(("mtr", "mt", "meter", "metre")):
        return "m"
    if u in ("ft", "feet", "foot"):
        return "ft"
    if u.startswith("inch"):
        return "in"
    if u.startswith("kg"):
        return "kg"
    if u == "g" or u.startswith(("gm", "gram")):
        return "g"
    if u == "l" or u.startswith(("ltr", "litre", "liter")):
        return "l"
    if u.startswith(("pc", "piece")):
        return "pc"
    if u == "w" or u.startswith("watt"):
        return "w"
    return u


def variant_sig(text):
    out = set()
    pat = (r"(\d+(?:\.\d+)?)\s*(mtrs?|meters?|metres?|mts?|cm|mm|ft|feet|foot|"
           r"inch(?:es)?|kgs?|gms?|grams?|ml|ltrs?|litres?|liters?|pcs|pc|"
           r"pieces?|pack|set|watts?|mah|gb|m|g|l|w)\b")
    for m in re.finditer(pat, text, re.I):
        num = float(m.group(1))
        u = norm_unit(m.group(2))
        if u in ("pc", "pack", "set") and num > 24:
            continue
        out.add("%g%s" % (num, u))
    return ",".join(sorted(out))


def check_image(url):
    try:
        im = Image.open(io.BytesIO(get(url)))
        if min(im.size) < 150:
            return None
        im = im.convert("L").resize((9, 8))
        px = list(im.getdata())
        bits = 0
        for r in range(8):
            for c in range(8):
                bits = (bits << 1) | (1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0)
        return bits
    except Exception:
        return None


def check_many(urls):
    urls = list(dict.fromkeys(urls))
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        return dict(zip(urls, ex.map(check_image, urls)))


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
    tm0 = re.search(
        r'tgme_channel_info_header_title[^>]*>\s*(?:<span[^>]*>)?(.*?)</', page, re.S)
    title = clean(tm0.group(1)) if tm0 else ""
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
        text = ""
        for cls, inner in re.findall(
                r'<div class="(tgme_widget_message_text[^"]*)"[^>]*>(.*?)</div>', b, re.S):
            if "reply" in cls:
                continue
            text = clean(inner)
            break
        dm = re.search(r'<time[^>]*datetime="([^"]+)"', b)
        date = dm.group(1) if dm else ""
        if not images and thumbs:
            images = thumbs
        images = [html.unescape(u) for u in images]
        if not images:
            continue
        items.append({
            "id": "%s/%d" % (h, num), "num": num, "channel": h,
            "text": text, "images": images,
            "video": html.unescape(videos[0]) if videos else "", "date": date,
        })
    return {"items": items, "title": title,
            "min": min(nums) if nums else None,
            "max": max(nums) if nums else None,
            "oldest": min(dates) if dates else None}


def close(a, b):
    da, db = parse_date(a), parse_date(b)
    return bool(da and db and abs((da - db).total_seconds()) <= 90)


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
    now_ts = int(time.time())
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
            if raw.get("version") == VERSION:
                products = raw["products"]
                skipped = dict.fromkeys(raw.get("skipped", []))
                state = raw.get("state", {})
                pending = raw.get("pending", [])
            else:
                print("Old data format found, starting fresh")
        except Exception as e:
            print("raw.json problem:", e)

    price_fixed = 0
    fallback_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)
    for p in products:
        srcs = p.get("sources", {})
        txt = p.get("raw", "")
        ch = p.get("raw_ch")
        if not srcs or not txt or ch not in srcs:
            continue
        try:
            latest = max(srcs, key=lambda c: parse_date(srcs[c].get("date", "")) or fallback_dt)
        except Exception:
            latest = ch
        if latest != ch:
            continue
        new_p = find_price(txt)
        if new_p and new_p != (srcs[ch].get("price") or 0):
            srcs[ch]["price"] = new_p
            price_fixed += 1
    print("price-migration fixed:", price_fixed, "products")

    pending = [g for g in pending if g["channel"].lower() in hset]
    for p in products:
        p["sources"] = {c: s for c, s in p["sources"].items() if c.lower() in hset}
    products = [p for p in products if p["sources"]]

    index = {}
    for p in products:
        for hh in p["hashes"]:
            index[(hh, p.get("ntext", ""), p.get("sig", ""))] = p

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
            if r["title"]:
                s["title"] = r["title"]
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
        todo_ch = [h for h in handles
                   if not state[h]["done"] and state[h]["cursor"]
                   and fails.get(h, 0) < 3]
        if not todo_ch:
            break
        for h in todo_ch:
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

    corpus = {}
    for p in products:
        ch = p.get("raw_ch") or next(iter(p["sources"]))
        corpus.setdefault(ch, []).append(p["raw"])
    for g in pending:
        corpus.setdefault(g["channel"], []).append(g["text"])
    ctx = build_ctx(handles, state, corpus, block_re, cores)

    pending.sort(key=lambda g: g["date"], reverse=True)
    todo = []
    for g in pending:
        g["text"] = redact(g["text"])
        d = parse_date(g["date"])
        price = find_price(g["text"])
        if not d or d < limit or price is None or not g["images"]:
            for pp in g["posts"]:
                skipped[pp] = None
            continue
        g["price"] = price
        todo.append(g)

    rest = []
    new_count = upd_count = bad_imgs = 0
    i = 0
    while i < len(todo):
        if elapsed() > WORK_LIMIT:
            rest = todo[i:]
            break
        batch = todo[i:i + BATCH]
        i += BATCH
        res = check_many([u for g in batch for u in g["images"]])
        for g in batch:
            valid = [u for u in g["images"] if res.get(u) is not None]
            if not valid:
                for pp in g["posts"]:
                    skipped[pp] = None
                bad_imgs += 1
                continue
            h = res[valid[0]]
            g["images"] = valid
            ctext = clean_text(g["text"], g["channel"], ctx)
            ntext = re.sub(r"\s+", " ", ctext.lower()).strip()
            sig = variant_sig(g["text"])
            k = (h, ntext, sig)
            src = {"price": g["price"], "date": g["date"], "post": g["posts"][0]}
            match = index.get(k)
            if match:
                prev = match.get("bdate") or max(
                    s["date"] for s in match["sources"].values())
                old = match["sources"].get(g["channel"])
                if not old or parse_date(g["date"]) >= parse_date(old["date"]):
                    match["sources"][g["channel"]] = src
                match["posts"] += g["posts"]
                if parse_date(g["date"]) >= parse_date(prev):
                    match["images"] = g["images"]
                    match["video"] = g["video"]
                    match["raw"] = g["text"][:1500]
                    match["raw_ch"] = g["channel"]
                    match["bdate"] = g["date"]
                    match["chk"] = now_ts
                    match["fail"] = 0
                upd_count += 1
            else:
                p = {
                    "pid": g["posts"][0], "hashes": [h], "ntext": ntext,
                    "sig": sig, "raw": g["text"][:1500], "raw_ch": g["channel"],
                    "images": g["images"], "video": g["video"],
                    "bdate": g["date"], "chk": now_ts, "fail": 0,
                    "sources": {g["channel"]: src}, "posts": list(g["posts"]),
                }
                products.append(p)
                index[k] = p
                new_count += 1
    pending = rest

    removed = 0
    if elapsed() < WORK_LIMIT and products:
        order = sorted(products, key=lambda p: p.get("chk", 0))[:RECHECK]
        res = check_many([p["images"][0] for p in order])
        for p in order:
            p["chk"] = now_ts
            if res.get(p["images"][0]) is not None:
                p["fail"] = 0
                continue
            p["fail"] = p.get("fail", 0) + 1
            if p["fail"] >= 2:
                alt = check_many(p["images"][1:])
                good = [u for u in p["images"][1:] if alt.get(u) is not None]
                if good:
                    p["images"] = good
                    p["fail"] = 0
                else:
                    p["dead"] = True
        n0 = len(products)
        products = [p for p in products if not p.get("dead")]
        removed = n0 - len(products)

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
        ch = p.get("raw_ch") or next(iter(p["sources"]))
        text = clean_text(p["raw"], ch, ctx)
        title = clean_name(text)
        out.append({
            "id": make_id(p["pid"]), "title": title, "text": text,
            "images": p["images"], "video": p["video"], "price": sell,
            "date": latest["date"],
        })
    out.sort(key=lambda x: x["date"], reverse=True)

    for p in products:
        p["raw"] = redact(p.get("raw", ""))
    for g in pending:
        g["text"] = redact(g.get("text", ""))
    for o in out:
        o["text"] = redact(o.get("text", ""))
        o["title"] = redact(o.get("title", ""))

    json.dump({"version": VERSION, "products": products,
               "skipped": list(skipped.keys())[-20000:],
               "state": state, "pending": pending},
              open(RAW, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"whatsapp": st["whatsapp"], "shop_name": st["shop_name"],
               "order_url": st["order_url"]},
              open(CONF, "w", encoding="utf-8"), ensure_ascii=False)

    backlog = len([h for h in handles if not state[h]["done"]])
    boiler_n = sum(len(v) for v in ctx["boiler"].values())
    print("DONE. new:", new_count, "exact-duplicates-merged:", upd_count,
          "live:", len(out), "hidden(>2000):", hidden,
          "| price-fixed:", price_fixed,
          "| skipped(no working photo):", bad_imgs,
          "| removed(dead photo):", removed,
          "| waiting to process:", len(pending),
          "| channels still backfilling:", backlog,
          "| footer/name lines auto-blocked:", boiler_n,
          "| failed:", len(failed),
          "| minutes used:", round(elapsed() / 60, 1))
    if pending or backlog:
        print("PARTIAL: run again or wait for the next daily run to finish the rest")


main()
