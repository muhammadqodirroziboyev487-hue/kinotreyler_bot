# -*- coding: utf-8 -*-
"""
KinoTreylerUz_Bot v6 (Full Pro w/ SQLite, daily news, random, stats graph, premium users)
Requirements: pyTelegramBotAPI, Flask, APScheduler, matplotlib
"""

import os
import sqlite3
import json
import logging
import shutil
from datetime import datetime, date
from flask import Flask, request, Response
import telebot
from telebot import types
from apscheduler.schedulers.background import BackgroundScheduler
import matplotlib
matplotlib.use('Agg')  # no GUI
import matplotlib.pyplot as plt
import random
import time

# ---------------- CONFIG ----------------
TOKEN = "7681707411:AAG0OyCUYK5D9ihYiHg7v4NJ8FebK-2p1-A"  # <-- siz bergan token
ADMIN_ID = 912998145                                   # <-- siz bergan admin id
WEBHOOK_URL = "https://kinotreyleruz-bot-pq4y.onrender.com"  # <-- Render URL
DB_FILE = "bot.db"
BACKUP_DIR = "backups"
# ----------------------------------------

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot + Flask
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# Scheduler
scheduler = BackgroundScheduler()
scheduler.start()

# ---------------- DB helpers ----------------
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()
    # movies: id, name, file_id, genre, views, likes, dislikes, premium (0/1), added_at
    cur.execute("""
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        file_id TEXT NOT NULL,
        genre TEXT,
        views INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        dislikes INTEGER DEFAULT 0,
        premium INTEGER DEFAULT 0,
        added_at TEXT
    )
    """)
    # users: id, first_name, is_premium (0/1), lang, referrals, joined_at
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        first_name TEXT,
        is_premium INTEGER DEFAULT 0,
        lang TEXT DEFAULT 'uz',
        referrals INTEGER DEFAULT 0,
        joined_at TEXT
    )
    """)
    # channels: id INTEGER PRIMARY KEY AUTOINCREMENT, identifier TEXT
    cur.execute("""
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier TEXT UNIQUE
    )
    """)
    # news: id, type ('text','photo','video'), content (text or file_id), caption, scheduled (0/1)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT,
        content TEXT,
        caption TEXT,
        scheduled INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)
    # referrals: id, referrer_id, referred_id, created_at
    cur.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

init_db()

# ----------------- Basic DB operations -----------------
def add_user_db(user_id, first_name=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE id=?", (user_id,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (id, first_name, joined_at) VALUES (?, ?, ?)",
                    (user_id, first_name or "", datetime.utcnow().isoformat()))
        conn.commit()
    conn.close()

def set_user_premium(user_id, val=1):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET is_premium=? WHERE id=?", (1 if val else 0, user_id))
    conn.commit()
    conn.close()

def add_movie_db(name, file_id, genre=None, premium=0):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO movies (name, file_id, genre, premium, added_at) VALUES (?, ?, ?, ?, ?)",
                (name, file_id, genre or "", int(bool(premium)), datetime.utcnow().isoformat()))
    conn.commit()
    mid = cur.lastrowid
    conn.close()
    return mid

def delete_movie_db(mid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM movies WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return cur.rowcount

def list_movies_db(limit=100):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM movies ORDER BY id ASC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_movie_db(mid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM movies WHERE id=?", (mid,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def inc_movie_view(mid):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE movies SET views = views + 1 WHERE id=?", (mid,))
    conn.commit()
    conn.close()

def add_channel_db(identifier):
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO channels (identifier) VALUES (?)", (identifier,))
        conn.commit()
        ok = True
    except sqlite3.IntegrityError:
        ok = False
    conn.close()
    return ok

def del_channel_db(identifier):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM channels WHERE identifier=?", (identifier,))
    conn.commit()
    c = cur.rowcount
    conn.close()
    return c

def list_channels_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT identifier FROM channels ORDER BY id ASC")
    rows = [r["identifier"] for r in cur.fetchall()]
    conn.close()
    return rows

def add_news_db(kind, content, caption=None, scheduled=0):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO news (kind, content, caption, scheduled, created_at) VALUES (?, ?, ?, ?, ?)",
                (kind, content, caption or "", int(bool(scheduled)), datetime.utcnow().isoformat()))
    conn.commit()
    nid = cur.lastrowid
    conn.close()
    return nid

def list_news_db(scheduled=None):
    conn = get_conn()
    cur = conn.cursor()
    if scheduled is None:
        cur.execute("SELECT * FROM news ORDER BY id DESC")
    else:
        cur.execute("SELECT * FROM news WHERE scheduled=? ORDER BY id DESC", (int(bool(scheduled)),))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def add_referral(referrer, referred):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                (referrer, referred, datetime.utcnow().isoformat()))
    cur.execute("UPDATE users SET referrals = referrals + 1 WHERE id=?", (referrer,))
    conn.commit()
    conn.close()

# ---------------- Channel subscription check ----------------
def user_is_subscribed_all(user_id):
    channels = list_channels_db()
    if not channels:
        return True, []
    missing = []
    for ch in channels:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status in ("left", "kicked"):
                missing.append(ch)
        except Exception as e:
            logger.info("channel check error for %s on %s: %s", user_id, ch, e)
            missing.append(ch)
    return (len(missing) == 0), missing

# ---------------- Backup & daily cron tasks ----------------
def backup_db():
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"bot_db_backup_{ts}.db")
    try:
        shutil.copyfile(DB_FILE, dst)
        logger.info("Backup created: %s", dst)
    except Exception as e:
        logger.exception("Backup failed: %s", e)

def daily_news_job():
    # send scheduled news (scheduled=1) or send oldest unsent news as daily post
    news_list = list_news_db(scheduled=1)
    if not news_list:
        news_list = list_news_db(scheduled=0)
    # pick latest
    if not news_list:
        logger.info("No news to send today.")
        return
    item = news_list[0]
    kind = item.get("kind")
    content = item.get("content")
    caption = item.get("caption") or ""
    # send to all users
    users = []
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users")
    users = [r["id"] for r in cur.fetchall()]
    conn.close()
    sent = 0
    for u in users:
        try:
            if kind == "text":
                bot.send_message(u, caption or content)
            elif kind == "photo":
                bot.send_photo(u, content, caption=caption)
            elif kind == "video":
                bot.send_video(u, content, caption=caption)
            sent += 1
            time.sleep(0.05)
        except Exception as e:
            logger.info("Daily news send failed for %s: %s", u, e)
    logger.info("Daily news sent to %d users", sent)

# schedule backups and daily news
# daily at 00:00 UTC
scheduler.add_job(backup_db, 'cron', hour=0, minute=0)
scheduler.add_job(daily_news_job, 'cron', hour=0, minute=5)  # 00:05 UTC daily

# ---------------- Utilities ----------------
def ensure_user_registered(message):
    uid = message.from_user.id
    add_user_db(uid, getattr(message.from_user, "first_name", ""))
    # handle start parameter referral
    if message.text and message.text.startswith("/start "):
        parts = message.text.split()
        if len(parts) > 1:
            try:
                ref_id = int(parts[1])
                if ref_id != uid:
                    add_user_db(ref_id)
                    add_referral(ref_id, uid)
            except:
                pass

# ---------------- Keyboards ----------------
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎲 Tasodifiy kino", "🎞 Kinolar")
    kb.add("🔎 Qidiruv", "📢 Kanallar")
    kb.add("ℹ️ Yordam")
    return kb

def admin_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("/addmovie", "/delmovie")
    kb.add("/addchannel", "/delchannel")
    kb.add("/news_add", "/news_list")
    kb.add("/topmovies", "/stats")
    kb.add("/give_premium", "/revoke_premium")
    return kb

# ---------------- Command handlers ----------------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    # handle referral /start <refid>
    ensure_user_registered(message)
    add_user_db(message.from_user.id, message.from_user.first_name)
    txt = ("👋 Assalomu alaykum!\n\n"
           "🎬 KinoTreylerUz_Bot ga xush kelibsiz.\n"
           "Raqam yuboring yoki menyudan tanlang.\n")
    bot.send_message(message.chat.id, txt, reply_markup=main_keyboard())

@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Siz admin emassiz.")
        return
    bot.send_message(message.chat.id, "🔧 Admin keyboard:", reply_markup=admin_keyboard())

@bot.message_handler(commands=['addmovie'])
def cmd_addmovie(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faoliyat faqat admin uchun.")
        return
    # start flow: ask for name -> then media -> then genre -> premium?
    bot.send_message(message.chat.id, "🎬 Kinoning nomini yuboring:")
    bot.register_next_step_handler(message, _addmovie_name_step)

def _addmovie_name_step(message):
    name = message.text.strip()
    msg = bot.send_message(message.chat.id, "Endi video yoki fayl yuboring (video/document/animation):")
    bot.register_next_step_handler(msg, _addmovie_media_step, name)

def _addmovie_media_step(message, name):
    file_id = None
    if message.content_type == "video" and message.video:
        file_id = message.video.file_id
    elif message.content_type == "animation" and message.animation:
        file_id = message.animation.file_id
    elif message.content_type == "document" and message.document:
        file_id = message.document.file_id
    else:
        bot.send_message(message.chat.id, "❗ Video topilmadi. Jarayon bekor qilindi.")
        return
    bot.send_message(message.chat.id, "Janrini yozing (masalan: jangari, komediya):")
    bot.register_next_step_handler(message, _addmovie_genre_step, name, file_id)

def _addmovie_genre_step(message, name, file_id):
    genre = message.text.strip()
    bot.send_message(message.chat.id, "Agar bu kino PREMIUM bo'lsa 'ha' yozing, aks holda 'yo'q':")
    bot.register_next_step_handler(message, _addmovie_premium_step, name, file_id, genre)

def _addmovie_premium_step(message, name, file_id, genre):
    ans = message.text.strip().lower()
    premium = 1 if ans in ("ha", "yes", "y") else 0
    mid = add_movie_db(name, file_id, genre, premium)
    bot.send_message(message.chat.id, f"✅ Kino qo'shildi: ID = {mid}\nNom: {name}\nJanr: {genre}\nPremium: {'Ha' if premium else 'Yo‘q'}")

@bot.message_handler(commands=['delmovie'])
def cmd_delmovie(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faoliyat faqat admin uchun.")
        return
    movies = list_movies_db()
    if not movies:
        bot.send_message(message.chat.id, "📭 Hozircha kinolar yo'q.")
        return
    txt = "🎞 Kinolar:\n" + "\n".join(f"{m['id']}. {m['name']}" for m in movies)
    bot.send_message(message.chat.id, txt)
    bot.send_message(message.chat.id, "O'chirmoqchi bo'lgan ID ni yuboring:")
    bot.register_next_step_handler(message, _delmovie_step)

def _delmovie_step(message):
    try:
        mid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "❗ Noto'g'ri ID.")
        return
    cnt = delete_movie_db(mid)
    if cnt:
        bot.send_message(message.chat.id, f"✅ {mid} o'chirildi.")
    else:
        bot.send_message(message.chat.id, "❌ Bunday ID topilmadi.")

# channels
@bot.message_handler(commands=['addchannel'])
def cmd_addchannel(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faoliyat faqat admin uchun.")
        return
    bot.send_message(message.chat.id, "Kanal username yoki chat_id yuboring (masalan @kanal yoki -100123...):")
    bot.register_next_step_handler(message, _addchannel_step)

def _addchannel_step(message):
    ch = message.text.strip()
    ok = add_channel_db(ch)
    if ok:
        bot.send_message(message.chat.id, f"✅ Kanal qo'shildi: {ch}")
    else:
        bot.send_message(message.chat.id, "⚠️ Kanal allaqachon mavjud yoki xato.")

@bot.message_handler(commands=['delchannel'])
def cmd_delchannel(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faoliyat faqat admin uchun.")
        return
    bot.send_message(message.chat.id, "O'chirish uchun kanal username yoki chat_id yuboring:")
    bot.register_next_step_handler(message, _delchannel_step)

def _delchannel_step(message):
    ch = message.text.strip()
    c = del_channel_db(ch)
    if c:
        bot.send_message(message.chat.id, f"✅ Kanal o'chirildi: {ch}")
    else:
        bot.send_message(message.chat.id, "❌ Kanal topilmadi.")

@bot.message_handler(commands=['news_add'])
def cmd_news_add(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faoliyat faqat admin uchun.")
        return
    bot.send_message(message.chat.id, "Yangilik turi (text/photo/video) ni yozing:")
    bot.register_next_step_handler(message, _news_kind_step)

def _news_kind_step(message):
    kind = message.text.strip().lower()
    if kind not in ("text", "photo", "video"):
        bot.send_message(message.chat.id, "❗ Faqat text/photo/video")
        return
    bot.send_message(message.chat.id, "Endi content yuboring (text matn yoki file_id):")
    bot.register_next_step_handler(message, _news_content_step, kind)

def _news_content_step(message, kind):
    content = None
    caption = ""
    if kind == "text":
        content = message.text or ""
    else:
        # expect file upload
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
            content = file_id
        elif message.content_type == "video":
            file_id = message.video.file_id
            content = file_id
        elif message.content_type == "document":
            content = message.document.file_id
        else:
            bot.send_message(message.chat.id, "❗ Iltimos fayl yuboring.")
            return
        bot.send_message(message.chat.id, "Caption (ixtiyoriy) yozing:")
        bot.register_next_step_handler(message, _news_caption_step, kind, content)
        return
    add_news_db(kind, content, caption="")
    bot.send_message(message.chat.id, "✅ Yangilik qo'shildi (text).")

def _news_caption_step(message, kind, content):
    caption = message.text or ""
    add_news_db(kind, content, caption)
    bot.send_message(message.chat.id, "✅ Yangilik qo'shildi.")

@bot.message_handler(commands=['news_list'])
def cmd_news_list(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faoliyat faqat admin uchun.")
        return
    items = list_news_db()
    if not items:
        bot.send_message(message.chat.id, "📭 Yangiliklar ro'yxati bo'sh.")
        return
    txt = "📰 Yangiliklar:\n" + "\n".join(f"{it['id']}. {it['kind']} - {it['caption'] or it['content'][:30]}" for it in items)
    bot.send_message(message.chat.id, txt)

# ---------------- Random movie ----------------
@bot.message_handler(regexp=r'^(🎲\s*Tasodifiy kino|/random|/rand)$')
def cmd_random(message):
    movies = list_movies_db()
    if not movies:
        bot.send_message(message.chat.id, "📭 Kinolar yo'q.")
        return
    movie = random.choice(movies)
    # subscription & premium check
    ok, missing = user_is_subscribed_all(message.from_user.id)
    if not ok:
        kb = types.InlineKeyboardMarkup()
        for ch in missing:
            if isinstance(ch, str) and ch.startswith("@"):
                kb.add(types.InlineKeyboardButton(ch, url=f"https://t.me/{ch.lstrip('@')}"))
            else:
                kb.add(types.InlineKeyboardButton(str(ch), callback_data="noop"))
        kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check_subs"))
        bot.send_message(message.chat.id, "⚠️ Avval kanallarga a'zo bo'ling:", reply_markup=kb)
        return
    # premium check
    if movie.get("premium"):
        # check user premium
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT is_premium FROM users WHERE id=?", (message.from_user.id,))
        r = cur.fetchone()
        conn.close()
        if not r or r["is_premium"] != 1:
            bot.send_message(message.chat.id, "🔒 Bu kino premium, faqat premium foydalanuvchilar ko‘ra oladi.")
            return
    try:
        bot.send_video(message.chat.id, movie["file_id"], caption=f"🎬 {movie['name']}\n\nJanr: {movie['genre']}")
        inc_movie_view(movie["id"])
    except Exception as e:
        logger.exception("Random send error: %s", e)
        bot.send_message(message.chat.id, "⚠️ Kino yuborishda xatolik.")

# ---------------- Search by text & get movie by ID ----------------
@bot.message_handler(func=lambda m: m.text and m.text.strip().isdigit())
def get_by_id(message):
    mid = int(message.text.strip())
    movie = get_movie_db(mid)
    if not movie:
        bot.send_message(message.chat.id, "❌ Bunday ID topilmadi.")
        return
    ok, missing = user_is_subscribed_all(message.from_user.id)
    if not ok:
        kb = types.InlineKeyboardMarkup()
        for ch in missing:
            if isinstance(ch, str) and ch.startswith("@"):
                kb.add(types.InlineKeyboardButton(ch, url=f"https://t.me/{ch.lstrip('@')}"))
            else:
                kb.add(types.InlineKeyboardButton(str(ch), callback_data="noop"))
        kb.add(types.InlineKeyboardButton("✅ Tekshirish", callback_data="check_subs"))
        bot.send_message(message.chat.id, "⚠️ Avval kanallarga a'zo bo'ling:", reply_markup=kb)
        return
    if movie.get("premium"):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT is_premium FROM users WHERE id=?", (message.from_user.id,))
        r = cur.fetchone()
        conn.close()
        if not r or r["is_premium"] != 1:
            bot.send_message(message.chat.id, "🔒 Bu kino premium, faqat premium foydalanuvchilar ko‘ra oladi.")
            return
    try:
        bot.send_video(message.chat.id, movie["file_id"], caption=f"🎬 {movie['name']}\n\nJanr: {movie['genre']}")
        inc_movie_view(mid)
    except Exception as e:
        logger.exception("send movie error: %s", e)
        bot.send_message(message.chat.id, "⚠️ Kino yuborishda xatolik.")

@bot.message_handler(func=lambda m: m.text and len(m.text.strip()) > 1 and not m.text.strip().isdigit())
def search_text(message):
    q = message.text.strip().lower()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM movies WHERE lower(name) LIKE ? OR lower(genre) LIKE ? LIMIT 50", (f"%{q}%", f"%{q}%"))
    rows = cur.fetchall()
    conn.close()
    if not rows:
        bot.send_message(message.chat.id, "🔍 Hech narsa topilmadi.")
        return
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"{r['id']}. {r['name'][:40]}", callback_data=f"get_{r['id']}"))
    bot.send_message(message.chat.id, f"🔎 Topildi {len(rows)} ta natija:", reply_markup=kb)

# ---------------- Callback handler ----------------
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    data = call.data or ""
    uid = call.from_user.id
    if data == "check_subs":
        ok, missing = user_is_subscribed_all(uid)
        if ok:
            bot.answer_callback_query(call.id, "✅ A'zo bo'lgansiz")
            bot.send_message(uid, "🎉 Rahmat! Endi kinoning raqamini yuboring.")
        else:
            bot.answer_callback_query(call.id, "❌ Hali ham a'zo emassiz.")
        return
    if data.startswith("get_"):
        mid = int(data.split("_",1)[1])
        movie = get_movie_db(mid)
        if not movie:
            bot.answer_callback_query(call.id, "Kino topilmadi.")
            return
        ok, missing = user_is_subscribed_all(uid)
        if not ok:
            bot.answer_callback_query(call.id, "Avval kanallarga a'zo bo'ling.", show_alert=True)
            return
        if movie.get("premium"):
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT is_premium FROM users WHERE id=?", (uid,))
            r = cur.fetchone()
            conn.close()
            if not r or r["is_premium"] != 1:
                bot.answer_callback_query(call.id, "Bu kino premium.", show_alert=True)
                return
        try:
            bot.send_video(uid, movie["file_id"], caption=f"🎬 {movie['name']}\n\nJanr: {movie['genre']}")
            inc_movie_view(mid)
            bot.answer_callback_query(call.id, "Kino yuborildi.")
        except Exception as e:
            logger.exception("callback send error: %s", e)
            bot.answer_callback_query(call.id, "Xatolik yuz berdi.")
        return

# ---------------- Admin stats & topmovies (graph) ----------------
@bot.message_handler(commands=['topmovies'])
def cmd_topmovies(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faqat admin uchun.")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name, views FROM movies ORDER BY views DESC LIMIT 10")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        bot.send_message(message.chat.id, "📭 Hech qanday ma'lumot yo'q.")
        return
    names = [r["name"][:30] for r in rows]
    views = [r["views"] for r in rows]
    # plot
    plt.figure(figsize=(8,4))
    plt.barh(range(len(names))[::-1], views[::-1])
    plt.yticks(range(len(names))[::-1], names[::-1])
    plt.xlabel("Ko'rishlar soni")
    plt.title("Top 10 ko'p ko'rilgan kinolar")
    img_path = f"topmovies_{int(time.time())}.png"
    plt.tight_layout()
    plt.savefig(img_path)
    plt.close()
    with open(img_path, "rb") as img:
        bot.send_photo(message.chat.id, img, caption="📊 Top 10")
    os.remove(img_path)

@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faqat admin uchun.")
        return
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM users")
    users_cnt = cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) as cnt FROM movies")
    movies_cnt = cur.fetchone()["cnt"]
    conn.close()
    bot.send_message(message.chat.id, f"👥 Foydalanuvchilar: {users_cnt}\n🎞 Kinolar: {movies_cnt}\n📁 Backup papka: {BACKUP_DIR}")

# ---------------- Give / revoke premium ----------------
@bot.message_handler(commands=['give_premium'])
def cmd_give_premium(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faqat admin uchun.")
        return
    bot.send_message(message.chat.id, "User ID ni yuboring (premium berish uchun):")
    bot.register_next_step_handler(message, _give_premium_step)

def _give_premium_step(message):
    try:
        uid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "❗ Noto'g'ri ID.")
        return
    set_user_premium(uid, 1)
    bot.send_message(message.chat.id, f"✅ {uid} ga premium berildi.")

@bot.message_handler(commands=['revoke_premium'])
def cmd_revoke_premium(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faqat admin uchun.")
        return
    bot.send_message(message.chat.id, "User ID ni yuboring (premium olib qo'yish uchun):")
    bot.register_next_step_handler(message, _revoke_premium_step)

def _revoke_premium_step(message):
    try:
        uid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "❗ Noto'g'ri ID.")
        return
    set_user_premium(uid, 0)
    bot.send_message(message.chat.id, f"✅ {uid} premium olib tashlandi.")

# ---------------- Broadcast (manual news send) ----------------
@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if message.from_user.id != ADMIN_ID:
        bot.send_message(message.chat.id, "⛔ Faqat admin.")
        return
    bot.send_message(message.chat.id, "📣 Xabar matnini yuboring (text):")
    bot.register_next_step_handler(message, _broadcast_step)

def _broadcast_step(message):
    text = message.text or ""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users")
    users = [r["id"] for r in cur.fetchall()]
    count = 0
    for u in users:
        try:
            bot.send_message(u, text)
            count += 1
            time.sleep(0.05)
        except Exception as e:
            logger.info("Broadcast failed for %s: %s", u, e)
    bot.send_message(message.chat.id, f"✅ Xabar yuborildi: {count} ta foydalanuvchiga.")

# ---------------- Webhook endpoints ----------------
@app.route(f"/{TOKEN}", methods=['POST'])
def webhook():
    try:
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    except Exception as e:
        logger.exception("Webhook error: %s", e)
    return Response("OK", status=200)

@app.route("/", methods=['GET'])
def index():
    return "<b>KinoTreylerUz_Bot PRO (v6) - Webhook running</b>"

# ---------------- Set webhook and start ---------------
def set_webhook():
    url = WEBHOOK_URL.rstrip("/") + "/" + TOKEN
    try:
        bot.remove_webhook()
    except:
        pass
    try:
        res = bot.set_webhook(url=url)
        logger.info("set_webhook result: %s", res)
        return res
    except Exception as e:
        logger.exception("set_webhook failed: %s", e)
        return False

if __name__ == "__main__":
    # ensure DB init
    init_db()
    # set webhook
    set_webhook()
    # run flask
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
