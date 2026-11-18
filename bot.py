import os, re, requests
from bs4 import BeautifulSoup
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (ApplicationBuilder, CallbackQueryHandler,
                          ContextTypes, MessageHandler, filters)

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


def get_stats(path, stat_idx):
    r = requests.get("https://api.scraperapi.com/",
                     params={"api_key": os.environ["SCRAPER_KEY"],
                             "url": BASE + path, "render": "true"},
                     timeout=90)
    soup = BeautifulSoup(r
