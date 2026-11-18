import os, re, requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (ApplicationBuilder, ContextTypes,
                          MessageHandler, filters)

BASE = "https://www.transfermarkt.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}
# positions after the opponent name: games, W, D, L, goals, assists, yellow
GAMES, GOALS, YELLOW = 0, 4, 6


def find_player(name):
    r = requests.get(f"{BASE}/schnellsuche/ergebnis/schnellsuche",
                     params={"query": name}, headers=HEADERS, timeout=15)
    a = BeautifulSoup(r.text, "html.parser").select_one('a[href*="/profil/spieler/"]')
    if not a:
        return None
    return a.get_text(strip=True), a["href"].replace("/profil/spieler/", "/bilanz/spieler/")


def num(x):
    x = x.strip()
    return int(x) if x.isdigit() else 0


def get_stats(path):
    r = requests.get("https://api.scraperapi.com/",
                     params={"api_key": os.environ["SCRAPER_KEY"],
                             "url": BASE + path, "render": "true"},
                     timeout=90)
    soup = BeautifulSoup(r.text, "html.parser")
    rows, seen = [], set()
    club_links = soup.find_all("a", href=re.compile("/verein/"))
    for a in club_links:
        name = a.get_text(strip=True)
        if not name or name in seen:
            continue
        node = a
        for _ in range(8):
            node = node.parent
            if node is None:
                break
            strs = list(node.stripped_strings)
            if name not in strs:
                continue
            tail = strs[strs.index(name) + 1: strs.index(name) + 9]
            if len(tail) >= 7 and all(re.fullmatch(r"\d+|-", t) for t in tail[:7]):
                seen.add(name)
                rows.append((name, num(tail[GAMES]),
                             num(tail[GOALS]), num(tail[YELLOW])))
                break
    debug = (f"status={r.status_code}, size={len(r.text)}, "
             f"clublinks={len(club_links)}, rows={len(rows)}")
    return rows, debug


def top5_with_ties(rows, col):
    rows = [r for r in rows if r[col] > 0]
    rows.sort(key=lambda r: (-r[col], r[1]))
    if len(rows) <= 5:
        return rows
    cutoff = rows[4][col]
    return [r for r in rows if r[col] >= cutoff]
