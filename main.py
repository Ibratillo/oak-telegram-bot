# main.py
# Telegram bot: oak.uz himoya e'lonlarini lotincha, chiroyli va ishonchli yuboradi.
# Muallif: ChatGPT (Ibratillo uchun) — yangilangan versiya

import os
import json
import logging
import asyncio
from html import unescape

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()

# ======= SOZLAMALAR =======
BOT_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("❌ TELEGRAM_TOKEN topilmadi! .env faylini tekshiring (TELEGRAM_TOKEN=...).")

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
if ADMIN_CHAT_ID:
    try:
        ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
    except ValueError:
        logging.warning("ADMIN_CHAT_ID butun son emas, e'tiborga olinmaydi.")
        ADMIN_CHAT_ID = None

CHANNELS = ["@oak_himoya_elonlari"]
NEWS_LIST_URL = "https://oak.uz/page/8"
LAST_FILE = "last_news.json"
SETTINGS_FILE = "settings.json"
DEFAULT_IMAGE = "img.png"

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ======= LOGGING =======
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ======= JSON yordamchi =======
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.exception("JSON load error: %s", e)
    return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logger.exception("JSON save error")

settings = load_json(SETTINGS_FILE, {"admin_lang": "latin"})
last_data = load_json(LAST_FILE, {"links": []})

# ======= Translit =======
CYR_TO_LAT = {
    'А':'A','а':'a','Б':'B','б':'b','В':'V','в':'v','Г':'G','г':'g','Д':'D','д':'d',
    'Е':'E','е':'e','Ё':'Yo','ё':'yo','Ж':'J','ж':'j','З':'Z','з':'z','И':'I','и':'i',
    'Й':'Y','й':'y','К':'K','к':'k','Л':'L','л':'l','М':'M','м':'m','Н':'N','н':'n',
    'О':'O','о':'o','П':'P','п':'p','Р':'R','р':'r','С':'S','с':'s','Т':'T','т':'t',
    'У':'U','у':'u','Ф':'F','ф':'f','Х':'X','х':'x','Ц':'Ts','ц':'ts','Ч':'Ch','ч':'ch',
    'Ш':'Sh','ш':'sh','Щ':'Shch','щ':'shch','Ъ':'’','ъ':'’','Ь':'','ь':'',
    'Э':'E','э':'e','Ю':'Yu','ю':'yu','Я':'Ya','я':'ya',
    'Қ':'Q','қ':'q','Ғ':'Gʻ','ғ':'gʻ','Ў':'Oʻ','ў':'oʻ','Ҳ':'H','ҳ':'h',
    '’':"'", 'ʼ':"'", '«':'"','»':'"','“':'"','”':'"'
}

def translit_to_latin(text: str) -> str:
    if not text:
        return text
    out = []
    for ch in text:
        out.append(CYR_TO_LAT.get(ch, ch))
    return ''.join(out).replace("ʼ","'").replace("`","'")

# ======= HTML -> text =======
def clean_html_text(tag):
    if tag is None:
        return ""
    txt = tag.get_text(separator='\n', strip=True)
    return unescape(txt)

# ======= Fetch news (sync) =======
def fetch_latest_news_raw():
    sess = requests.Session()
    try:
        r = sess.get(NEWS_LIST_URL, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        logger.exception("HTTP error while fetching list: %s", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []

    for li in soup.select("ul.nav > li"):
        try:
            title_tag = li.select_one("div.title h3 a")
            date_tag = li.select_one("ul.meta li a")
            content_tag = li.select_one("div.post--content")
            if not title_tag:
                continue

            title_raw = clean_html_text(title_tag)
            date_raw = clean_html_text(date_tag) if date_tag else ""
            content_raw = clean_html_text(content_tag) if content_tag else ""
            link = title_tag.get("href", "").strip()
            if link and not link.startswith("http"):
                link = "https://oak.uz" + link

            image_url = None
            if link:
                try:
                    ar = sess.get(link, headers=HEADERS, timeout=10)
                    if ar.ok:
                        sa = BeautifulSoup(ar.text, "html.parser")
                        m = sa.find("meta", property="og:image") or sa.find("meta", attrs={"name":"twitter:image"})
                        if m and m.get("content"):
                            image_url = m.get("content")
                except Exception:
                    image_url = None

            results.append({
                "title": title_raw,
                "date": date_raw,
                "content": content_raw,
                "link": link,
                "image": image_url
            })
        except Exception:
            logger.exception("Error parsing an item, skip it.")
            continue

    return results

# ======= Prepare message =======
def prepare_message(item: dict, to_latin: bool = True, max_chars=900):
    title = item.get("title","").strip()
    date = item.get("date","").strip()
    content = item.get("content","").strip()
    link = item.get("link","")
    image = item.get("image") or DEFAULT_IMAGE

    paras = [p.strip() for p in content.splitlines() if p.strip()]

    if len(paras) >= 2 and len(paras[0]) < 100:
        paras[0] = paras[0] + " " + paras[1]
        del paras[1]

    selected = []
    if len(paras) >= 1:
        selected.append(paras[0])
    if len(paras) >= 3:
        selected.append(paras[2])

    summary = "\n\n".join(selected).strip()
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."

    if not title:
        return None

    if to_latin:
        title_out = translit_to_latin(title)
        summary_out = translit_to_latin(summary)
        date_out = translit_to_latin(date)
    else:
        title_out = title
        summary_out = summary
        date_out = date

    caption = (
        f"✨ <b>{title_out}</b>\n"
        f"🗓 {date_out}\n\n"
        f"🧾 {summary_out}\n\n"
        f"📎 Kanal: {CHANNELS[0]}"
    )

    if len(caption) > 900:
        caption = caption[:900].rstrip() + "..."

    return {"caption": caption, "link": link, "image": image}

# ======= Telegram sending =======
async def send_news_items(bot: Bot, items: list, to_latin=True):
    sent_links = last_data.get("links", [])
    new_links = []
    sent_count = 0

    for item in items[:10]:
        prepared = prepare_message(item, to_latin=to_latin)
        if not prepared:
            continue
        link = prepared["link"]
        if not link or link in sent_links:
            continue

        img = prepared["image"] or DEFAULT_IMAGE
        caption = prepared["caption"]
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📖 Batafsil", url=link)]])

        try:
            await bot.send_photo(chat_id=CHANNELS[0], photo=img, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            for ch in CHANNELS[1:]:
                await bot.send_photo(chat_id=ch, photo=img, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            sent_links.append(link)
            new_links.append(link)
            sent_count += 1
        except Exception:
            logger.exception("Send error, fallback to text.")
            try:
                await bot.send_message(chat_id=CHANNELS[0], text=caption, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                logger.exception("Fallback send failed.")

    last_data["links"] = sent_links[-5000:]
    save_json(LAST_FILE, last_data)
    return sent_count, new_links

def admin_pref_is_latin():
    return settings.get("admin_lang","latin") == "latin"

# ======= Handlers =======
async def start(update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Assalomu alaykum! Bot ishga tushdi. /setlang_lotin yoki /setlang_krill")

async def setlang_latin(update, context):
    if ADMIN_CHAT_ID and update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID:
        settings["admin_lang"] = "latin"
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text("Admin til: LOTINCHA o'rnatildi.")
    else:
        await update.message.reply_text("Siz admin emassiz yoki ADMIN_CHAT_ID noto'g'ri sozlangan.")

async def setlang_kiril(update, context):
    if ADMIN_CHAT_ID and update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID:
        settings["admin_lang"] = "kiril"
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text("Admin til: KRILCHA o'rnatildi.")
    else:
        await update.message.reply_text("Siz admin emassiz yoki ADMIN_CHAT_ID noto'g'ri sozlangan.")

async def last_cmd(update, context):
    bot = context.bot
    # Sync fetch bajarilishini event-loopni bloklamaslik uchun threadda ishga tushiramiz
    raw = await asyncio.to_thread(fetch_latest_news_raw)
    to_latin = admin_pref_is_latin()
    count = 0
    for item in raw[:10]:
        prepared = prepare_message(item, to_latin=to_latin)
        if not prepared:
            continue
        try:
            await bot.send_photo(chat_id=update.effective_chat.id, photo=prepared["image"] or DEFAULT_IMAGE,
                                 caption=prepared["caption"], parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📖 Batafsil", url=prepared["link"])]]))
        except Exception:
            await bot.send_message(chat_id=update.effective_chat.id, text=prepared["caption"], parse_mode="HTML")
        count += 1
    if count == 0:
        await bot.send_message(chat_id=update.effective_chat.id, text="Yangilik topilmadi yoki ularning hammasi oldin yuborilgan.")

# ======= Job =======
async def periodic_job(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    try:
        raw = await asyncio.to_thread(fetch_latest_news_raw)
        if not raw:
            if ADMIN_CHAT_ID:
                await bot.send_message(ADMIN_CHAT_ID, "BILDIRGI: Yangilik topilmadi (fetch bo'sh).")
            return
        sent_count, new_links = await send_news_items(bot, raw, to_latin=True)
        if ADMIN_CHAT_ID:
            if sent_count > 0:
                await bot.send_message(ADMIN_CHAT_ID, f"BILDIRGI: {sent_count} ta yangilik kanalga yuborildi.")
            else:
                await bot.send_message(ADMIN_CHAT_ID, "BILDIRGI: Yangi yangilik yo'q.")
    except Exception as e:
        logger.exception("Periodic job error: %s", e)
        if ADMIN_CHAT_ID:
            try:
                await bot.send_message(ADMIN_CHAT_ID, f"Periodic job xatosi: {e}")
            except Exception:
                pass

# ======= MAIN =======
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang_lotin", setlang_latin))
    app.add_handler(CommandHandler("setlang_krill", setlang_kiril))
    app.add_handler(CommandHandler("last", last_cmd))

    # har 10 daqiqada (60s)
    app.job_queue.run_repeating(periodic_job, interval=60, first=10)

    logger.info("Bot ishga tushmoqda...")
    # run_polling() coroutine sifatida await qilinadi — shu bilan event-loop to'g'ri ishlaydi
    await app.run_polling()


if __name__ == "__main__":
    # TOZA SYNC CHAQIRUV — event loop allaqachon mavjud bo'lsa xato bermaydi
    import logging

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        from telegram.ext import Application

        app = Application.builder().token(BOT_TOKEN).build()

        # Handlers
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("setlang_lotin", setlang_latin))
        app.add_handler(CommandHandler("setlang_krill", setlang_kiril))
        app.add_handler(CommandHandler("last", last_cmd))

        # Job
        app.job_queue.run_repeating(periodic_job, interval=60, first=10)

        logger.info("Bot ishga tushmoqda...")
        # run_polling() sync tarzda chaqiriladi
        app.run_polling()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
