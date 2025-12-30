# main.py
# Telegram bot: oak.uz himoya e'lonlarini lotincha, chiroyli va ishonchli yuboradi.
# Muallif: ChatGPT (Ibratillo uchun)

import requests
from bs4 import BeautifulSoup
import json
import os
from html import unescape
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import Application, CommandHandler, ContextTypes
import logging

# ================== SOZLAMALAR ==================
# BOT_TOKEN = "5733325519:AAFv2ppID87NXf1iz-K_iPeI4_sPcYqRPNs"
CHANNELS = ["@oak_himoya_elonlari"]
NEWS_LIST_URL = "https://oak.uz/page/8"   # yoki /pages/27300 bo'lsa moslang
LAST_FILE = "last_news.json"
SETTINGS_FILE = "settings.json"
ADMIN_CHAT_ID = 1294422362  # Siz o'zgartirsangiz bo'ladi
DEFAULT_IMAGE = "img.png"  # default rasm

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# ================ LOGGING (mahalliy) ================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# =================== Yordamchi funksiyalar ===================

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.exception("JSON load error: %s", e)
    return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# Settings: admin language (latin/kiril)
settings = load_json(SETTINGS_FILE, {"admin_lang": "latin"})
last_data = load_json(LAST_FILE, {"links": []})  # {"links": [ ... ]}

# =================== Transliteratsiya (kril -> lotin Oʻzbek) ===================
# Oddiy va yetarli darajadagi xarakter-by-xarakter translit
CYR_TO_LAT = {
    'А':'A','а':'a','Б':'B','б':'b','В':'V','в':'v','Г':'G','г':'g','Д':'D','д':'d',
    'Е':'E','е':'e','Ё':'Yo','ё':'yo','Ж':'J','ж':'j','З':'Z','з':'z','И':'I','и':'i',
    'Й':'Y','й':'y','К':'K','к':'k','Л':'L','л':'l','М':'M','м':'m','Н':'N','н':'n',
    'О':'O','о':'o','П':'P','п':'p','Р':'R','р':'r','С':'S','с':'s','Т':'T','т':'t',
    'У':'U','у':'u','Ф':'F','ф':'f','Х':'X','х':'x','Ц':'Ts','ц':'ts','Ч':'Ch','ч':'ch',
    'Ш':'Sh','ш':'sh','Щ':'Shch','щ':'shch','Ъ':'’','ъ':'’','Ь':'','ь':'',
    'Э':'E','э':'e','Ю':'Yu','ю':'yu','Я':'Ya','я':'ya',
    # O'zbek-specific
    'Қ':'Q','қ':'q','Ғ':'Gʻ','ғ':'gʻ','Ў':'Oʻ','ў':'oʻ','Ҳ':'H','ҳ':'h',
    '’':"'", 'ʼ':"'", '«':'"','»':'"','“':'"','”':'"'
}

def translit_to_latin(text: str) -> str:
    """Kiril matnini oddiy tarzda lotincha o'giradi.
       Agar matnda lotincha belgilari bo'lsa, ular qoldiriladi."""
    if not text:
        return text
    out = []
    for ch in text:
        out.append(CYR_TO_LAT.get(ch, ch))
    # Qo'shimcha tozalash: takroriy apostroflarni yagona ' ga aylantirish
    return ''.join(out).replace("ʼ","'").replace("`","'")

# =================== HTML to clean text ===================
def clean_html_text(tag):
    """Tag ichidagi matnni olib, <br> larni yangi qatorga aylantiradi."""
    if tag is None:
        return ""
    # get_text(separator='\n') converts <br> into '\n'
    txt = tag.get_text(separator='\n', strip=True)
    return unescape(txt)

# =================== Yangiliklarni olish ===================
def fetch_latest_news_raw():
    """Sahifadagi yangilik elementlarini olish — list-of-dicts.
       Har bir element: {'title':..., 'link':..., 'date':..., 'content':..., 'image':...}"""
    sess = requests.Session()
    try:
        r = sess.get(NEWS_LIST_URL, headers=HEADERS, timeout=15)
        # r.encoding = 'utf-8'  # requests odatda avtomatik aniqlaydi; qo'yish mumkin
    except Exception as e:
        logger.exception("HTTP error: %s", e)
        return []

    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    # post items list selector - sahifadagi strukturaga mos
    for li in soup.select("ul.nav > li"):
        try:
            title_tag = li.select_one("div.title h3 a")
            date_tag = li.select_one("ul.meta li a")
            content_tag = li.select_one("div.post--content")
            link_tag = title_tag

            if not title_tag or not link_tag:
                continue

            title_raw = clean_html_text(title_tag)
            date_raw = clean_html_text(date_tag) if date_tag else ""
            content_raw = ""
            if content_tag:
                # content_tag may contain nested <p> with <br>, preserve newlines
                content_raw = clean_html_text(content_tag)
            link = link_tag.get("href", "").strip()
            if link and not link.startswith("http"):
                link = "https://oak.uz" + link

            # Try to fetch article page to get image (og:image)
            image_url = None
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

# =================== Xabar tayyorlash (tilga qarab) ===================
def prepare_message(item: dict, to_latin: bool = True, max_chars=900):
    """item: dictionary from fetch_latest_news_raw.
       to_latin: agar True bo'lsa title va content lotincha chiqariladi (translit).
       Qoidalar:
         - <br> -> newline (all done earlier)
         - 1-,3-,4- abzastlarni oladi
         - agar 1-abzast <100 belgi bo'lsa 2-abzastni qo'shadi (va 2-abzast o'chiriladi)
    """
    title = item.get("title","").strip()
    date = item.get("date","").strip()
    content = item.get("content","").strip()
    link = item.get("link","")
    image = item.get("image") or DEFAULT_IMAGE

    # Split paragraphs: content may contain extra blank lines; splitlines keeps order
    paras = [p.strip() for p in content.splitlines() if p.strip()]

    # Agar 1-paragraf 100 ta belgidan kam bo'lsa 2-paragrafni unga qo'shamiz
    if len(paras) >= 2 and len(paras[0]) < 100:
        paras[0] = paras[0] + " " + paras[1]
        del paras[1]

    # Tanlash: 1-,3-,4- (index 0,2,3)
    selected = []
    if len(paras) >= 1:
        selected.append(paras[0])
    if len(paras) >= 3:
        selected.append(paras[2])
    # if len(paras) >= 4:
    #     selected.append(paras[3])

    summary = "\n\n".join(selected).strip()
    # Qisqartirish agar juda uzun bo'lsa
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip() + "..."

    # Agar title bo'sh bo'lsa (xato yoki kategoriya sarlavhasi), return None — yubormaymiz
    if not title or title.lower().strip() in ("o‘zbekiston Respublikasi Oliy ta’lim, fan va innovatsiyalar vazirligi","o‘zbekiston Respublikasi oliy ta’lim, fan va innovatsiyalar vazirligi"):
        return None

    # Tilga qarab translit
    if to_latin:
        title_out = translit_to_latin(title)
        summary_out = translit_to_latin(summary)
        date_out = translit_to_latin(date)
    else:
        title_out = title
        summary_out = summary
        date_out = date

    # Tuzilish: chiroyli va HTML safe
    caption = (
        f"✨ <b>{title_out}</b>\n"
        f"🗓 {date_out}\n\n"
        f"🧾 {summary_out}\n\n"
        # f"🔗 <a href=\"{link}\">Batafsil o'qish</a>\n"
        f"📎 Kanal: @oak_himoya_elonlari"
    )
    # Caption length check for send_photo (Telegram limit ~1024). We'll ensure shorter.
    if len(caption) > 900:
        caption = caption[:900].rstrip() + "..."

    return {
        "caption": caption,
        "link": link,
        "image": image
    }

# =================== Telegramga yuborish ===================
async def send_news_items(bot: Bot, items: list, to_latin=True):
    """items: list of item dicts from fetch_latest_news_raw
       to_latin: channel messages lotincha bo'ladimi (ha)"""
    sent_links = last_data.get("links", [])
    new_links = []
    sent_count = 0

    # Saralash: birinchi 10 ta element (eng yangi)
    for item in items[:10]:
        prepared = prepare_message(item, to_latin=to_latin)
        if not prepared:
            continue
        link = prepared["link"]
        if not link:
            continue
        if link in sent_links:
            continue  # oldindan yuborilgan
        # send
        img = prepared["image"] or DEFAULT_IMAGE
        caption = prepared["caption"]

        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📖 Batafsil", url=link)]])
        try:
            # Avval rasm bilan caption yuboramiz
            await bot.send_photo(chat_id=CHANNELS[0], photo=img, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            # Agar bir nechta kanallar bo'lsa, boshqa kanallarga ham yuborish
            for ch in CHANNELS[1:]:
                await bot.send_photo(chat_id=ch, photo=img, caption=caption, parse_mode="HTML", reply_markup=keyboard)
            sent_links.append(link)
            new_links.append(link)
            sent_count += 1
        except Exception as e:
            logger.exception("Send error: %s", e)
            # Fallback: text-only
            try:
                await bot.send_message(chat_id=CHANNELS[0], text=caption, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                logger.exception("Fallback send failed.")
    # Saqlash: oxirgi 50 linkni saqlaymiz
    last_data["links"] = sent_links[-5000:]
    save_json(LAST_FILE, last_data)
    return sent_count, new_links

# =================== Admin xabar tilini tekshirish ===================
def admin_pref_is_latin():
    return settings.get("admin_lang","latin") == "latin"

# =================== Bot buyruqlari ===================
async def start(update: object, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Assalomu alaykum! Bot ishga tushdi. Admin tilini o'zgartirish uchun /setlang_lotin yoki /setlang_krill buyrug'idan foydalaning.")

async def setlang_latin(update: object, context: ContextTypes.DEFAULT_TYPE):
    # Faqat admin ruxsatiga tekshir: update.effective_chat.id == ADMIN_CHAT_ID
    if update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID:
        settings["admin_lang"] = "latin"
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text("Admin til: LOTINCHA o'rnatildi.")
    else:
        await update.message.reply_text("Siz admin emassiz.")

async def setlang_kiril(update: object, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID:
        settings["admin_lang"] = "kiril"
        save_json(SETTINGS_FILE, settings)
        await update.message.reply_text("Admin til: KRILCHA o'rnatildi.")
    else:
        await update.message.reply_text("Siz admin emassiz.")

async def last_cmd(update: object, context: ContextTypes.DEFAULT_TYPE):
    """/last komandasi — oxirgi 10 ta yangilikni ko'rsatadi (adminga sozlangan tilda)."""
    bot = context.bot
    raw = fetch_latest_news_raw()
    to_latin = admin_pref_is_latin()
    # Tayyorlanib admin chatga yuboriladi (matn ko'p bo'lsa bir nechta xabar)
    count = 0
    for item in raw[:10]:
        prepared = prepare_message(item, to_latin=to_latin)
        if not prepared:
            continue
        try:
            await bot.send_photo(chat_id=update.effective_chat.id, photo=prepared["image"] or DEFAULT_IMAGE,
                                 caption=prepared["caption"], parse_mode="HTML",
                                 reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📖 Batafsil", url=prepared["link"])]]) )
        except Exception:
            # fallback to text
            await bot.send_message(chat_id=update.effective_chat.id, text=prepared["caption"], parse_mode="HTML")
        count += 1
    if count == 0:
        await bot.send_message(chat_id=update.effective_chat.id, text="Yangilik topilmadi yoki ularning hammasi oldin yuborilgan.")

# =================== Davriy vazifa (job) ===================
async def periodic_job(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    try:
        raw = fetch_latest_news_raw()
        if not raw:
            await bot.send_message(ADMIN_CHAT_ID, "BILDIRGI: Yangilik topilmadi (fetch_latest_news_raw bo'sh).")
            return
        # Kanaldagi xabarlar doimo lotincha bo'lsin (siz xohladingiz)
        sent_count, new_links = await send_news_items(bot, raw, to_latin=True)
        if sent_count > 0:
            await bot.send_message(ADMIN_CHAT_ID, f"BILDIRGI: {sent_count} ta yangilik kanalga yuborildi.")
        else:
            await bot.send_message(ADMIN_CHAT_ID, "BILDIRGI: Yangi yangilik yo'q.")
    except Exception as e:
        logger.exception("Periodic job error: %s", e)
        try:
            await bot.send_message(ADMIN_CHAT_ID, f"Periodic job xatosi: {e}")
        except Exception:
            pass

# =================== MAIN ===================
def main():
    # Application yaratamiz
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setlang_lotin", setlang_latin))
    app.add_handler(CommandHandler("setlang_krill", setlang_kiril))
    app.add_handler(CommandHandler("last", last_cmd))

    # Job: har 10 daqiqada tekshiradi (600 soniya)
    app.job_queue.run_repeating(periodic_job, interval=600, first=10)

    # Ishga tushurish
    logger.info("Bot ishga tushmoqda...")
    app.run_polling()

if __name__ == "__main__":
    main()
