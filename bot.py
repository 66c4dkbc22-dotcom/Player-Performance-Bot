import os, requests
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
# column offsets after the opponent-name cell
GAMES, GOALS, YELLOW = 1, 5, 7


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
    r = requests.get(BASE + path, headers=HEADERS, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for tr in soup.select("table.items tr"):
        tds = tr.find_all("td")
        links = [a for a in tr.select("a") if a.get_text(strip=True)]
        if not links or len(tds) < 8:
            continue
        link = links[0]
        i = next((n for n, td in enumerate(tds) if link in td.find_all("a")), None)
        if i is None or i + stat_idx >= len(tds):
            continue
        rows.append((link.get_text(strip=True),
                     num(tds[i + stat_idx].get_text()),
                     num(tds[i + GAMES].get_text())))
    title = soup.title.get_text(strip=True)[:60] if soup.title else "none"
    debug = (f"status={r.status_code}, size={len(r.text)}, "
             f"tables={len(soup.select('table.items'))}, rows={len(rows)}, "
             f"title={title}, url={path}")
    return rows, debug


def top5_with_ties(rows):
    rows = [r for r in rows if r[1] > 0]
    rows.sort(key=lambda r: (-r[1], r[2]))
    if len(rows) <= 5:
        return rows
    cutoff = rows[4][1]
    return [r for r in rows if r[1] >= cutoff]


async def on_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    found = find_player(update.message.text)
    if not found:
        await update.message.reply_text("Couldn't find that player.")
        return
    ctx.user_data["player"] = found
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚽ Goals", callback_data="goals"),
        InlineKeyboardButton("🟨 Yellow cards", callback_data="yellow")]])
    await update.message.reply_text(f"{found[0]} - what do you want to check?",
                                    reply_markup=kb)


async def on_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if "player" not in ctx.user_data:
        await q.edit_message_text("Send a player name first.")
        return
    name, path = ctx.user_data["player"]
    kind = q.data
    all_rows, debug = get_stats(path, GOALS if kind == "goals" else YELLOW)
    rows = top5_with_ties(all_rows)
    label = "goals" if kind == "goals" else "yellow cards"
    if not rows:
        await q.edit_message_text(f"No {label} found for {name}.\n\nDEBUG: {debug}")
        return
    lines = [f"{name} - most {label} against:"]
    for team, count, games in rows:
        lines.append(f"{team}: {count} in {games} games")
    await q.edit_message_text("\n".join(lines))


app = ApplicationBuilder().token(os.environ["BOT_TOKEN"]).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_name))
app.add_handler(CallbackQueryHandler(on_choice))
app.run_polling()
