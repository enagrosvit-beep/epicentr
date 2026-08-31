#!/usr/bin/env python3
"""Генерує epicentr_stock.xml з каталогу Гарбуза (Horoshop API).

Запускається з GitHub Actions щодня о 21:00 UTC — за 3 години до вікна,
у яке Епіцентр забирає файл (00:00-02:00 Київ).

Захисники:
  * свіжий токен перед КОЖНОЮ сторінкою (TTL 600 с)
  * 429 приходить як HTTP 200 з тілом "Retry after N" — ловиться текстом
  * якщо позицій стало менше ніж на 5% — файл НЕ перезаписується
"""
import json, os, re, sys, time, urllib.request

HOST  = os.environ.get("HS_HOST", "harbuz.in.ua")
LOGIN = os.environ["HS_LOGIN"]
PASS  = os.environ["HS_PASS"]
OUT   = os.environ.get("OUT", "epicentr_stock.xml")
IN_STOCK_IDS = {1, 9}          # «В наявності» і дубль-статус «у наявності»
SHRINK_LIMIT = 0.05            # максимально допустиме скорочення каталогу


def api(path, body, timeout=300):
    req = urllib.request.Request(
        f"https://{HOST}/api/{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            print(f"  network error: {e}", flush=True)
            time.sleep(10)
            continue
        raw = json.dumps(data)
        if '"code": 429' in raw or '"code":429' in raw:
            m = re.search(r"after (\d+)", raw)
            wait = int(m.group(1)) + 10 if m else 60
            print(f"  rate limit, sleeping {wait}s", flush=True)
            time.sleep(wait)
            continue
        return data
    return None


def token():
    r = api("auth/", {"login": LOGIN, "password": PASS}, timeout=60)
    if not r or r.get("status") != "OK":
        sys.exit(f"auth failed: {json.dumps(r, ensure_ascii=False)[:200]}")
    return r["response"]["token"]


def pull_catalog():
    rows, offset = [], 0
    while True:
        data = api("catalog/export/", {"token": token(), "limit": 500, "offset": offset})
        if not data:
            sys.exit("export returned nothing")
        if data.get("status") != "OK":
            raw = json.dumps(data, ensure_ascii=False)
            if "UNAUTHORIZED" in raw:      # токен протух між сторінками
                time.sleep(3)
                continue
            sys.exit(f"export failed: {raw[:200]}")
        products = data["response"].get("products", [])
        if not products:
            break
        rows.extend(products)
        offset += len(products)
        print(f"  pulled {offset}", flush=True)
        if len(products) < 500:
            break
    return rows


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build(products):
    lines = ['<?xml version="1.0" encoding="UTF-8" ?>', "<yml_catalog>", "<offers>"]
    kept = skipped = 0
    for p in products:
        article = (p.get("article") or "").strip()
        price = p.get("price")
        if not article or price in (None, "", 0):
            skipped += 1
            continue
        available = (p.get("presence") or {}).get("id") in IN_STOCK_IDS
        lines.append(f'<offer id="{esc(article)}" available="{str(available).lower()}">')
        lines.append(f"<price>{price}</price>")
        lines.append("</offer>")
        kept += 1
    lines += ["</offers>", "</yml_catalog>", ""]
    return "\n".join(lines), kept, skipped


def count_offers(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().count("<offer ")


def main():
    print("pulling Harbuz catalog…", flush=True)
    products = pull_catalog()
    print(f"got {len(products)} products", flush=True)

    xml, kept, skipped = build(products)
    old = count_offers(OUT)
    print(f"offers: new={kept} old={old} skipped(no article/price)={skipped}", flush=True)

    if old and kept < old * (1 - SHRINK_LIMIT):
        sys.exit(f"REFUSING: каталог скоротився {old} → {kept} "
                 f"(>{int(SHRINK_LIMIT*100)}%). Схоже на неповний витяг.")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"wrote {OUT}: {kept} offers", flush=True)


if __name__ == "__main__":
    main()
