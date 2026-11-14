import os
import re
import html
import random
import time
from datetime import datetime, timedelta, timezone
import asyncio
import logging

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("moviebot")

# ---------- CONFIG (Railway Variables) ----------
BOT_TOKEN = os.environ["BOT_TOKEN"]
TMDB_TOKEN = os.environ["TMDB_TOKEN"]  # TMDB "API Read Access Token"
# One or more channels, comma separated: -1001234567890,@yourchannel2
CHANNEL_IDS = [x.strip() for x in os.environ["CHANNEL_ID"].split(",") if x.strip()]
CHANNEL_ID = CHANNEL_IDS[0]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

DOWNLOAD_URL = os.environ.get("DOWNLOAD_URL", "https://moviemanialk.com")  # fixed for every post
WHATSAPP_URL = os.environ.get(
    "WHATSAPP_URL", "https://whatsapp.com/channel/0029Vb6dIi242DccwGQfTr2R"
)
FB_URL = os.environ.get(
    "FB_URL", "https://www.facebook.com/profile.php?id=61578959931824"
)

# ---- Auto-post (Firebase admin panel) ----
FIREBASE_DB_URL = os.environ.get("FIREBASE_DB_URL", "https://cineflix1-1fdc6-default-rtdb.firebaseio.com").rstrip("/")
FIREBASE_API_KEY = os.environ.get("FIREBASE_API_KEY", "AIzaSyAh6zMB65iWqt3R2Ep0C5_7eV8ZaUHI7t8")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "120"))
SITE_URL = os.environ.get("SITE_URL", "https://www.moviemanialk.com").rstrip("/")
SUB_BY = os.environ.get("SUB_BY", "Piratelk.com")
REQUEST_BOT_URL = os.environ.get("REQUEST_BOT_URL", "https://t.me/Moviemanialkrequest_bot")
POSTED_PATH = "telegram_bot/posted"
POSTED: set = set()  # ids already handled (shared by auto-post + /add)

TMDB = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/w500"
HEADERS = {"Authorization": f"Bearer {TMDB_TOKEN}", "accept": "application/json"}


# ---------- TMDB ----------
async def tmdb_get(path: str, **params):
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{TMDB}{path}", headers=HEADERS, params=params)
        r.raise_for_status()
        return r.json()


async def search(query: str):
    data = await tmdb_get("/search/multi", query=query, include_adult="false")
    results = [x for x in data.get("results", []) if x.get("media_type") in ("movie", "tv")]
    return results[:8]


async def details(media_type: str, tmdb_id: int):
    return await tmdb_get(
        f"/{media_type}/{tmdb_id}", append_to_response="credits,videos,images",
        include_image_language="en,null",
    )


# ---------- POST BUILDER ----------
SEP = "━━━━━━━━━━━━━━━━━━"
SINHALA_LINE = "🇱🇰 <b>සිංහල උපසිරස සමග Download කරගැනිමට හා Online නැරඹිමට පිවිසෙන්න</b> 👇"


def stars(rating: float) -> str:
    full = int(round(rating / 2))
    return "★" * full + "☆" * (5 - full)


def hashtags(genres: list, year: str) -> str:
    tags = []
    for g in genres[:3]:
        t = "".join(ch for ch in g.title() if ch.isalnum())
        if t:
            tags.append("#" + t)
    tags.append("#SinhalaSub")
    if year:
        tags.append(f"#{year}")
    return " ".join(tags)


def render_card(title, year, kind, rating, genres, runtime, quality, country,
                director, cast, overview, language="") -> str:
    """Simple post: title, short description, genre / language / rating, then the watch line."""
    e = html.escape
    head = f"{kind} <b>{e(str(title))}" + (f" ({e(str(year))})" if year else "") + " With Sinhala Subtitles</b>"
    info = []
    if genres:
        info.append("🎭 <b>ශානරය:</b> " + e(", ".join(genres)))
    if language:
        info.append("🗣 <b>භාෂාව:</b> " + e(str(language)))
    if rating:
        info.append(f"⭐ <b>Rating:</b> {rating:.1f}/10")
    tail = ["", "\n".join(info), "", "👇 <b>සිංහල සබ් එක්කම බලන්න:</b>"] if info else ["", "👇 <b>සිංහල සබ් එක්කම බලන්න:</b>"]
    overview = (overview or "").strip()
    limit = 450
    while True:
        body = [head]
        if overview:
            ov = overview if len(overview) <= limit else overview[: limit - 3].rsplit(" ", 1)[0] + "..."
            body += ["", e(ov)]
        text = "\n".join(body + tail)
        if len(text) <= 1024 or limit <= 60:
            return text
        limit -= 40


def tmdb_language(d: dict) -> str:
    langs = d.get("spoken_languages") or []
    if langs:
        return langs[0].get("english_name") or langs[0].get("name") or ""
    return d.get("original_language") or ""


def build_caption(d: dict, media_type: str) -> str:
    title = d.get("title") or d.get("name") or "Unknown"
    date = d.get("release_date") or d.get("first_air_date") or ""
    if media_type == "movie":
        mins = d.get("runtime") or 0
        runtime = f"{mins // 60}h {mins % 60}m" if mins else ""
    else:
        eps = d.get("episode_run_time") or []
        runtime = f"{eps[0]}m / ep" if eps else ""
    credits = d.get("credits", {})
    cast = ", ".join(c["name"] for c in credits.get("cast", [])[:5])
    directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]
    return render_card(
        title, date[:4], "🎬" if media_type == "movie" else "📺",
        d.get("vote_average") or 0,
        [g["name"] for g in d.get("genres", [])],
        runtime, "", "", ", ".join(directors[:2]), cast, d.get("overview"),
        language=tmdb_language(d),
    )


def trailer_url(d: dict):
    for v in d.get("videos", {}).get("results", []):
        if v.get("site") == "YouTube" and v.get("type") == "Trailer":
            return f"https://www.youtube.com/watch?v={v['key']}"
    return None


def btn(text: str, url: str, style: str | None = None) -> InlineKeyboardButton:
    """URL button with optional colour: primary=blue, success=green, danger=red."""
    if not style:
        return InlineKeyboardButton(text, url=url)
    try:
        return InlineKeyboardButton(text, url=url, style=style)
    except TypeError:  # older python-telegram-bot without native `style`
        return InlineKeyboardButton(text, url=url, api_kwargs={"style": style})


def post_keyboard(download_url: str, trailer: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🎬 WATCH NOW ON MOVIEMANIALK", download_url, "danger")],
        [btn("💬 FOLLOW WHATSAPP CHANNEL", WHATSAPP_URL, "success")],
    ])


async def send_post(bot, chat_id, post: dict, download_url: str):
    kb = post_keyboard(download_url, post["trailer"])
    if post["poster"]:
        return await bot.send_photo(
            chat_id, post["poster"], caption=post["caption"],
            parse_mode="HTML", reply_markup=kb,
        )
    return await bot.send_message(
        chat_id, post["caption"], parse_mode="HTML", reply_markup=kb
    )


# ---------- HELPERS ----------
def is_admin(update: Update) -> bool:
    uid = update.effective_user.id if update.effective_user else 0
    return uid in ADMIN_IDS


# ---------- HANDLERS ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        kb = InlineKeyboardMarkup([
            [btn("🌐 Website", SITE_URL, "primary")],
            [
                btn("💬 WhatsApp", WHATSAPP_URL, "success"),
                btn("👍 Facebook", FB_URL, "primary"),
            ],
        ])
        await update.message.reply_text(
            "🎬 <b>MovieMania LK</b>\n\n"
            "සිංහල උපසිරස සමග Movies & TV Series 🇱🇰\n\n"
            "📩 ඔයාට ඕන Movie / Series එකේ නම මෙතන type කරන්න, අපි ඒක add කරන්නම්!",
            parse_mode="HTML", reply_markup=kb,
        )
        return
    await update.message.reply_text(
        "👋 <b>Admin Panel</b>\n\n"
        "➕ /add – Movie එකක් site එකට add කරන්න (TMDB)\n"
        "🔎 Movie නමක් type කරලා manual post එකක් හදන්න\n\n"
        "<b>Commands</b>\n"
        "/autopost on|off – auto post පාලනය\n"
        "/add – movie එකක් site එකට add කරන්න\n"
        "/stats – සංඛ්‍යා ලේඛන\n"
        "/last – අන්තිමට post කළ 5\n"
        "/requests – members ලාගේ requests\n"
        "/find &lt;name&gt; – site එකේ movie හොයන්න\n"
        "/version – bot version\n"
        "/vip &lt;id&gt; – VIP on/off\n"
        "/del &lt;id&gt; – movie delete\n"
        "/repost &lt;id&gt; – movie එකක් ආයෙ post කරන්න",
        parse_mode="HTML",
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your ID: {update.effective_user.id}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await handle_request(update, context)
        return
    text = update.message.text.strip()

    if context.user_data.get("state") in ("dl_tg", "dl_url", "dl_size"):
        await handle_dl_input(update, context, text)
        return

    # Add-to-site flow (waiting for links / player / description / subtitle by ...)
    if context.user_data.get("state") in ("add_links", "add_sub", "add_desc", "add_subby", "add_player"):
        await handle_add_input(update, context, text)
        return

    # Step 1: search
    context.user_data.clear()
    try:
        results = await search(text)
    except Exception:
        log.exception("search failed")
        await update.message.reply_text("⚠️ TMDB error. පස්සේ try කරන්න.")
        return
    if not results:
        await update.message.reply_text("😕 කිසිවක් හම්බුනේ නෑ. වෙන නමකින් try කරන්න.")
        return

    lines = ["🔎 <b>Results</b> — අංකය තොරන්න:\n"]
    buttons = []
    for i, r in enumerate(results, 1):
        t = r.get("title") or r.get("name")
        y = (r.get("release_date") or r.get("first_air_date") or "")[:4]
        icon = "🎬" if r["media_type"] == "movie" else "📺"
        lines.append(f"{i}. {icon} {html.escape(t)} ({y}) ⭐{r.get('vote_average', 0):.1f}")
        buttons.append(
            InlineKeyboardButton(str(i), callback_data=f"pick:{r['media_type']}:{r['id']}")
        )
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    await update.message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if update.effective_user.id not in ADMIN_IDS:
        return
    data = q.data
    if data.startswith("rq:"):
        await handle_req_action(update, context)
        return
    if data.startswith(("add:", "del:", "vip:")):
        await handle_site_action(update, context)
        return

    if data.startswith("pick:"):
        _, media_type, tmdb_id = data.split(":")
        d = await details(media_type, int(tmdb_id))
        poster = f"{IMG}{d['poster_path']}" if d.get("poster_path") else None
        context.user_data["post"] = {
            "caption": build_caption(d, media_type),
            "poster": poster,
            "trailer": trailer_url(d),
        }
        context.user_data["download_url"] = DOWNLOAD_URL
        context.user_data["tmdb"] = (d, media_type)
        await send_post(context.bot, update.effective_chat.id, context.user_data["post"], DOWNLOAD_URL)
        await q.message.reply_text(
            "👆 Preview එක. මොකක් කරමුද?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("➕ Site එකට Add කරන්න", callback_data="add:start")],
                    [
                        InlineKeyboardButton("📣 Channel post only", callback_data="post"),
                        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                    ],
                ]
            ),
        )

    elif data == "post":
        post = context.user_data.get("post")
        url = context.user_data.get("download_url")
        if not post or not url:
            await q.message.reply_text("⚠️ Session expire. ආයෙ නමක් එවන්න.")
            return
        ok, failed = 0, []
        for ch in CHANNEL_IDS:
            try:
                await send_post(context.bot, ch, post, url)
                ok += 1
            except Exception as ex:
                log.exception("post failed for %s", ch)
                failed.append(f"{ch}: {ex}")
        if failed:
            await q.edit_message_text(
                f"✅ {ok}/{len(CHANNEL_IDS)} channel වලට post කළා.\n❌ Failed:\n" + "\n".join(failed)
                + "\nBot එක channel admin ද බලන්න."
            )
        else:
            await q.edit_message_text(f"✅ Channel {ok}කටම post කළා!")
        context.user_data.clear()

    elif data == "cancel":
        context.user_data.clear()
        await q.edit_message_text("❌ Cancel කළා.")


# ---------- PREMIUM EXTRAS ----------
AUTOPOST = True
REQ_COOLDOWN = 60
_last_req: dict = {}


_requests: dict = {}   # rid -> record (cache)
_req_msgs: dict = {}   # rid -> [(admin_chat_id, message_id)]

STATUS_LINE = {
    "pending": "⏳ <b>Pending</b>",
    "seen": "👀 <b>Seen</b>",
    "added": "✅ <b>Added</b>",
    "rejected": "❌ <b>Not available</b>",
}
STATUS_ICON = {"pending": "⏳", "seen": "👀", "added": "✅", "rejected": "❌"}


def req_text(rec: dict) -> str:
    line = STATUS_LINE.get(rec["status"], rec["status"])
    if rec.get("by") and rec["status"] != "pending":
        line += f" — {html.escape(rec['by'])}"
    return (
        "📩 <b>New Request</b>\n"
        f"🎬 {html.escape(rec['text'])}\n"
        f"👤 <a href=\"tg://user?id={rec['uid']}\">{html.escape(rec['name'])}</a>\n"
        f"📌 {line}"
    )


def req_keyboard(rec: dict):
    rid, st = rec["id"], rec["status"]
    if st == "pending":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("👀 Seen", callback_data=f"rq:seen:{rid}")],
            [
                InlineKeyboardButton("✅ Added", callback_data=f"rq:added:{rid}"),
                InlineKeyboardButton("❌ Not available", callback_data=f"rq:rejected:{rid}"),
            ],
        ])
    if st == "seen":
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Added", callback_data=f"rq:added:{rid}"),
            InlineKeyboardButton("❌ Not available", callback_data=f"rq:rejected:{rid}"),
        ]])
    return None
