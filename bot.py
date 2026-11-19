import os, re, asyncio, requests
from concurrent.futures import ThreadPoolExecutor
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

MAX_PLAYERS = 5      # players per message
CONCURRENCY = 3      # lookups running at the same time (keeps ScraperAPI happy)
# Set RENDER_FIRST=1 on Railway if the plain (no rendering) fetch never returns the table.
RENDER_FIRST = os.environ.get("RENDER_FIRST", "0") == "1"
SEP = "\n\n━━━━━━━━━━\n\n"

POOL = ThreadPoolExecutor(max_workers=CONCURRENCY)
PLAYER_CACHE = {}    # lowercase query -> (name, stats path), skips the search step on repeats


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


def fetch_rows(path, render):
    params = {"api_key": os.environ["SCRAPER_KEY"], "url": BASE + path}
    if render:
        params["render"] = "true"
    r = requests.get("https://api.scraperapi.com/", params=params,
                     timeout=90 if render else 45)
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
    debug = (f"render={render}, status={r.status_code}, size={len(r.text)}, "
             f"clublinks={len(club_links)}, rows={len(rows)}")
    return rows, debug


def get_stats(path):
    """Plain fetch first (much faster); falls back to rendering if no table came back."""
    if RENDER_FIRST:
        return fetch_rows(path, True)
    try:
        rows, debug = fetch_rows(path, False)
        if rows:
            return rows, debug
    except Exception as e:
        debug = f"plain failed: {e}"
    rows, debug2 = fetch_rows(path, True)
    return rows, f"{debug} | {debug2}"


def top5_with_ties(rows, col):
    rows = [r for r in rows if r[col] > 0]
    rows.sort(key=lambda r: (-r[col], r[1]))
    if len(rows) <= 5:
        return rows
    cutoff = rows[4][col]
    return [r for r in rows if r[col] >= cutoff]


def section(emoji, title, rows, col):
    if not rows:
        return f"{emoji} {title}\nNone found."
    lines = [f"{emoji} {title}"]
    for team, games, goals, yellow in rows:
        count = rows_value = r_val(team, games, goals, yellow, col)
        rate = count / games if games else 0
        if col == 2:
            lines.append(f"{team}: {count} in {games} games ({rate:.2f} per game)")
        else:
            lines.append(f"{team}: {count} in {games} games ({rate:.0%})")
    return "\n".join(lines)


def r_val(team, games, goals, yellow, col):
    return goals if col == 2 else yellow


def lookup(query):
    """Full lookup for one player, returns the text block for the combined reply."""
    try:
        key = query.lower()
        found = PLAYER_CACHE.get(key) or find_player(query)
        if not found:
            return f"Couldn't find {query}."
        PLAYER_CACHE[key] = found
        name, path = found
        rows, debug = get_stats(path)
        if not rows:
            return f"No data found for {name}.\n\nDEBUG: {debug}"
        goals = section("⚽", f"{name} - most goals against:",
                        top5_with_ties(rows, 2), 2)
        cards = section("🟨", f"{name} - most yellow cards against:",
                        top5_with_ties(rows, 3), 3)
        return f"{goals}\n\n{cards}"
    except Exception as e:
        return f"Error for {query}: {e}"


def parse_names(text):
    """'Kane, Haaland and Saka' or one name per line -> ['Kane', 'Haaland', 'Saka']."""
    parts = re.split(r"[,\n;]+|\s+and\s+", text, flags=re.IGNORECASE)
    names, seen = [], set()
    for p in parts:
        p = p.strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            names.append(p)
    return names


def chunk_blocks(blocks, limit=4000):
    """Join player blocks into messages under Telegram's length limit, splitting between players."""
    out, cur = [], ""
    for b in blocks:
        if not cur:
            cur = b[:limit]
        elif len(cur) + len(SEP) + len(b) > limit:
            out.append(cur)
            cur = b[:limit]
        else:
            cur += SEP + b
    if cur:
        out.append(cur)
    return out


async def on_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    names = parse_names(update.message.text)
    if not names:
        return
    note = None
    if len(names) > MAX_PLAYERS:
        note = f"Only the first {MAX_PLAYERS} players are looked up per message."
        names = names[:MAX_PLAYERS]
    msg = await update.message.reply_text("Looking up " + ", ".join(names) + "...")
    loop = asyncio.get_running_loop()
    blocks = await asyncio.gather(
        *[loop.run_in_executor(POOL, lookup, n) for n in names])
    blocks = list(blocks)
    if note:
        blocks.insert(0, note)
    parts = chunk_blocks(blocks)
    await msg.edit_text(parts[0])
    for p in parts[1:]:
        await update.message.reply_text(p)


app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_name))
app.run_polling()
