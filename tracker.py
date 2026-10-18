#!/usr/bin/env python3
"""One Piece TCG price tracker: scrape -> compare with previous -> notify."""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
CONFIG = BASE / "config.json"
STATE = BASE / "state.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
}
SYMBOLS = {"¥": "JPY", "￥": "JPY", "円": "JPY", "$": "USD", "£": "GBP", "€": "EUR"}
PRICE_RE = re.compile(r"(¥|￥|\$|£|€)\s?([\d,]+(?:\.\d+)?)|([\d,]+)\s?円")
DISPLAY = {"JPY": "¥", "USD": "$", "GBP": "£", "EUR": "€"}


def parse_money(text):
    """Return (amount, currency) from a text snippet, or None."""
    m = PRICE_RE.search(text or "")
    if not m:
        return None
    if m.group(1):
        return float(m.group(2).replace(",", "")), SYMBOLS[m.group(1)]
    return float(m.group(3).replace(",", "")), "JPY"


# ---------------------------------------------------------------------------
# Yuyu-tei (OPC section) — server-side rendered search results
# URL format: https://yuyu-tei.jp/sell/opc/s/search?search_word=ST21-001
# ---------------------------------------------------------------------------
def yuyu_tei_opc(url, card_number):
    """Search Yuyu-tei's One Piece section and return the lowest price
    for the given card number (e.g. 'ST21-001'). Returns (amount, 'JPY') or None."""
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    best = None
    # Each result block has: card number span, product link, price strong
    for card_num_span in soup.find_all(string=re.compile(rf"^{re.escape(card_number)}$")):
        # Walk up to find the price in the same result card
        parent = card_num_span.parent
        if parent is None:
            continue
        # Find the price strong within a reasonable scope
        price_el = parent.find_next("strong", class_=re.compile("d-block"))
        if price_el is None:
            price_el = parent.find("strong")
        if price_el:
            p = parse_money(price_el.get_text(" ", strip=True))
            if p and p[0] > 0:
                if best is None or p[0] < best[0]:
                    best = p
    return best


# ---------------------------------------------------------------------------
# SNKRDUNK — PSA 10 via JSON API
# ---------------------------------------------------------------------------
SNK = "https://snkrdunk.com/en"


def snkrdunk_psa10(url, name_filter=None):
    """PSA 10 lowest price via SNKRDUNK's JSON API (the site is JS-rendered).
    `url` may be a product page (/trading-cards/<id>) or a search URL
    (?keyword=...). For searches, [EN] printings are skipped, and
    `name_filter` (substring, e.g. "Gear 5" or "L-P") narrows to ONE card.
    Only the psa_10 entry is used; other grades are never substituted."""
    m = re.search(r"/trading-cards/(\d+)", url)
    if m:
        pid = m.group(1)
    else:
        kw = re.search(r"keyword=([^&]+)", url)
        if not kw:
            raise ValueError("snkrdunk url must be a /trading-cards/<id> or search?keyword= URL")
        r = requests.get(f"{SNK}/v1/search", headers=HEADERS, timeout=30,
                         params={"keyword": requests.utils.unquote(kw.group(1)),
                                 "page": 1, "perPage": 40})
        r.raise_for_status()
        items = [i for i in r.json().get("streetwears", [])
                 if i.get("isTradingCard") and "[EN]" not in i["name"]]
        if name_filter:
            items = [i for i in items if name_filter.lower() in i["name"].lower()]
        if len(items) != 1:
            opts = "; ".join(f"{i['id']}={i['name']}" for i in items) or "none"
            raise ValueError(f"search matched {len(items)} JP cards, need exactly 1 "
                             f"(set snkrdunk_filter or use a product URL). Candidates: {opts}")
        pid = items[0]["id"]
    r = requests.get(f"{SNK}/v2/products/SW---{pid}/min-prices", headers=HEADERS, timeout=30)
    r.raise_for_status()
    for e in r.json().get("entries", []):
        if e.get("id") == "psa_10":
            mp = e.get("minPrice") or {}
            if mp.get("value") is None:
                return None  # no PSA10 listed right now
            return float(mp["value"]), mp.get("currencyCode", "JPY")
    return None


def scrape(source, url, card=None):
    if source == "snkrdunk":
        return snkrdunk_psa10(url, (card or {}).get("snkrdunk_filter"))
    if source == "yuyu-tei":
        return yuyu_tei_opc(url, card["id"])
    if source == "tcgplayer":
        return tcgplayer_price(url)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    return parse_money(soup.get_text(" ", strip=True))


def tcgplayer_price(url):
    """Fetch TCGPlayer product details from the public mp-search-api endpoint.
    Extracts the product ID from a URL like
    https://www.tcgplayer.com/product/615565/one-piece-...
    Returns (lowestPrice, 'USD') or None."""
    m = re.search(r"/product/(\d+)/", url)
    if not m:
        raise ValueError("tcgplayer url must contain /product/<id>/")
    pid = m.group(1)
    h = dict(HEADERS)
    h.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tcgplayer.com/",
        "Origin": "https://www.tcgplayer.com",
    })
    r = requests.get(
        f"https://mp-search-api.tcgplayer.com/v1/product/{pid}/details",
        headers=h, timeout=30,
    )
    r.raise_for_status()
    d = r.json()
    lp = d.get("lowestPrice")
    if lp is None:
        return None
    return float(lp), "USD"


LANG = {"snkrdunk": "JP PSA10", "yuyu-tei": "JP", "tcgplayer": "EN"}


def fmt(amount, cur):
    sym = DISPLAY.get(cur, cur + " ")
    return f"{sym}{amount:,.0f}" if cur == "JPY" else f"{sym}{amount:,.2f}"


def notify(msg):
    print("NOTIFY:", msg)
    hook = os.getenv("DISCORD_WEBHOOK_URL")
    if hook:
        requests.post(hook, json={"content": msg}, timeout=15).raise_for_status()
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if tok and chat:
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": chat, "text": msg}, timeout=15).raise_for_status()


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    notify_first = cfg.get("notify_on_first_seen", False)
    errors = 0
    for card in cfg["cards"]:
        for source, url in card["sources"].items():
            if not url or url.startswith("PASTE"):
                continue  # not filled in yet
            key = f"{card['id']}|{source}"
            label = f"{card['id']} ({LANG.get(source, '?')})"
            if card.get("name"):
                label += f" {card['name']}"
            try:
                result = scrape(source, url, card)
                if not result:
                    raise ValueError("no price found on page")
            except Exception as e:  # keep previous price on failure
                errors += 1
                print(f"ERROR {key}: {e}", file=sys.stderr)
                continue
            amount, cur = result
            prev = state.get(key)
            if prev is None:
                if notify_first:
                    notify(f"{label}: first price on {source}: {fmt(amount, cur)}\n{url}")
            elif prev["currency"] != cur:
                print(f"NOTE {key}: currency {prev['currency']}->{cur}, not comparing")
            elif prev["price"] != amount:
                notify(f"{label}: Price changed on {source} from "
                       f"{fmt(prev['price'], prev['currency'])} to {fmt(amount, cur)}!\n{url}")
            state[key] = {"price": amount, "currency": cur, "updated": int(time.time())}
            print(f"OK {key}: {fmt(amount, cur)}")
            time.sleep(2)  # be polite
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Done. {errors} source(s) failed.")


if __name__ == "__main__":
    main()

