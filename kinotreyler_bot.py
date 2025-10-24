# -*- coding: utf-8 -*-
"""
KinoTreyler professional Telegram bot
- Webhook (Flask) bilan ishlaydi (Render.com uchun mos)
- SQLite bazasi: kinolar, foydalanuvchilar, kanallar, adminlar, referallar, yangiliklar
- Har kuni backup, kunlik yangiliklar, statistik grafik (matplotlib)
- Hech qayerda "chatgpt", "openai" yoki "gpt" so'zlari ishlatilmagan.
"""

import os
import sqlite3
import logging
import shutil
import random
from datetime import datetime
from typing import Tuple, List, Optional

from flask import Flask, request, Response
import telebot
from telebot import types
from apscheduler.schedulers.background import BackgroundScheduler
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------- CONFIG - bu yerda o'zgartiring yoki Render Environment'ga joylang ----------------
TOKEN = "8285142272:AAE1uUBowGTUoJYMDvZaqzjRweyWAlRQVLQ"   # <-- o'zingizning bot token
MAIN_ADMIN_ID = 912998145                                 # <-- asosiy admin id
WEBHOOK_URL = "https://kinotreyler-bot.onrender.com"      # <-- Render service URL (masalan https://my-service.onrender.com)
DB_FILE = "kinotreyler.db"
BACKUP_DIR = "backups"
PORT = int(os.environ.get("PORT", 10000))
# -----------------------------------------------------------------------------------------------

# Basic checks
if not TOKEN or MAIN_ADMIN_ID == 0:
    raise SystemExit("TOKEN va MAIN_ADMIN_ID ni to'g'ri belgilang.")

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot and Flask app
bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
app = Flask(__name__)

# Scheduler for backups and daily jobs
scheduler = BackgroundScheduler()
scheduler.start()

# ---------------- Database helpers ----------------
def get_conn():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    c = get_conn()
    cur = c.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS movies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT,
        file_id TEXT NOT NULL,
        genre TEXT,
        views INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        dislikes INTEGER DEFAULT 0,
        premium INTEGER DEFAULT 0,
        added_by INTEGER,
        added_at TEXT
    );
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        first_name TEXT,
        is_premium INTEGER DEFAULT 0,
        lang TEXT DEFAULT 'uz',
        theme TEXT DEFAULT 'day',
        referrals INTEGER DEFAULT 0,
        referred_by INTEGER,
        joined_at TEXT
    );
    CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        identifier TEXT UNIQUE
    );
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT,
        content TEXT,
        caption TEXT,
        scheduled INTEGER DEFAULT 0,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER,
        created_at TEXT
    );
    CREATE TABLE IF NOT EXISTS admins (
        id INTEGER PRIMARY KEY
    );
    """)
    # main admin
    cur.execute("INSERT OR IGNORE INTO admins (id) VALUES (?)", (MAIN_ADMIN_ID,))
    c.commit()
    c.close()

init_db()

# ---------------- Utility functions ----------------
def add_user_if_new(user):
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT id FROM users WHERE id=?", (user.id,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (id, first_name, joined_at) VALUES (?, ?, ?)",
                    (user.id, getattr(user, "first_name", "") or "", datetime.utcnow().isoformat()))
        c.commit()
    c.close()

def is_admin(user_id: int) -> bool:
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT id FROM admins WHERE id=?", (user_id,))
    r = cur.fetchone()
    c.close()
    return r is not None

def add_admin(user_id: int):
    c = get_conn()
    cur = c.cursor()
    cur.execute("INSERT OR IGNORE INTO admins (id) VALUES (?)", (user_id,))
    c.commit()
    c.close()

def remove_admin(user_id: int):
    c = get_conn()
    cur = c.cursor()
    cur.execute("DELETE FROM admins WHERE id=?", (user_id,))
    c.commit()
    c.close()

def add_channel(identifier: str) -> bool:
    c = get_conn()
    cur = c.cursor()
    try:
        cur.execute("INSERT INTO channels (identifier) VALUES (?)", (identifier,))
        c.commit()
        ok = True
    except sqlite3.IntegrityError:
        ok = False
    c.close()
    return ok

def remove_channel(identifier: str) -> int:
    c = get_conn()
    cur = c.cursor()
    cur.execute("DELETE FROM channels WHERE identifier=?", (identifier,))
    c.commit()
    cnt = cur.rowcount
    c.close()
    return cnt

def list_channels() -> List[str]:
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT identifier FROM channels ORDER BY id ASC")
    rows = [r["identifier"] for r in cur.fetchall()]
    c.close()
    return rows

def add_movie(name: str, description: str, file_id: str, genre: str=None, premium: int=0, added_by: int=None) -> int:
    c = get_conn()
    cur = c.cursor()
    cur.execute("""
        INSERT INTO movies (name, description, file_id, genre, premium, added_by, added_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, description or "", file_id, genre or "", int(bool(premium)), added_by, datetime.utcnow().isoformat()))
    c.commit()
    mid = cur.lastrowid
    c.close()
    return mid

def edit_movie(mid: int, **fields) -> int:
    c = get_conn()
    cur = c.cursor()
    allowed = {"name","description","file_id","genre","premium"}
    sets = []
    vals = []
    for k,v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        c.close(); return 0
    vals.append(mid)
    cur.execute(f"UPDATE movies SET {', '.join(sets)} WHERE id=?", tuple(vals))
    c.commit()
    cnt = cur.rowcount
    c.close()
    return cnt

def delete_movie(mid: int) -> int:
    c = get_conn()
    cur = c.cursor()
    cur.execute("DELETE FROM movies WHERE id=?", (mid,))
    c.commit()
    cnt = cur.rowcount
    c.close()
    return cnt

def get_movie(mid: int) -> Optional[dict]:
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT * FROM movies WHERE id=?", (mid,))
    r = cur.fetchone()
    c.close()
    return dict(r) if r else None

def list_movies(limit:int=200, offset:int=0, genre:Optional[str]=None, premium:Optional[int]=None) -> List[dict]:
    c = get_conn()
    cur = c.cursor()
    sql = "SELECT * FROM movies"
    cond = []
    params = []
    if genre:
        cond.append("lower(genre)=?")
        params.append(genre.lower())
    if premium is not None:
        cond.append("premium=?")
        params.append(1 if premium else 0)
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY id ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    cur.execute(sql, tuple(params))
    rows = [dict(r) for r in cur.fetchall()]
    c.close()
    return rows

def inc_view(mid:int):
    c = get_conn()
    cur = c.cursor()
    cur.execute("UPDATE movies SET views = views + 1 WHERE id=?", (mid,))
    c.commit()
    c.close()

def like_movie(mid:int):
    c = get_conn()
    cur = c.cursor()
    cur.execute("UPDATE movies SET likes = likes + 1 WHERE id=?", (mid,))
    c.commit()
    c.close()

def dislike_movie(mid:int):
    c = get_conn()
    cur = c.cursor()
    cur.execute("UPDATE movies SET dislikes = dislikes + 1 WHERE id=?", (mid,))
    c.commit()
    c.close()

def search_movies(q: str, limit: int=50) -> List[dict]:
    q2 = f"%{q.lower()}%"
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT * FROM movies WHERE lower(name) LIKE ? OR lower(description) LIKE ? OR lower(genre) LIKE ? LIMIT ?",
                (q2, q2, q2, limit))
    rows = [dict(r) for r in cur.fetchall()]
    c.close()
    return rows

def user_subscribed_all(user_id: int) -> Tuple[bool, List[str]]:
    channels = list_channels()
    if not channels:
        return True, []
    missing = []
    for ch in channels:
        try:
            member = bot.get_chat_member(ch, user_id)
            if member.status in ("left","kicked"):
                missing.append(ch)
        except Exception as e:
            logger.info("Check channel error %s %s", ch, e)
            missing.append(ch)
    return (len(missing) == 0), missing

# ---------------- Backups & scheduled jobs ----------------
def backup_db():
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"db_backup_{ts}.db")
    try:
        shutil.copyfile(DB_FILE, dst)
        logger.info("Backup created: %s", dst)
    except Exception as e:
        logger.exception("Backup failed: %s", e)

def generate_stats_plot(path="stats.png"):
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT name, views FROM movies ORDER BY views DESC LIMIT 10")
    rows = cur.fetchall()
    c.close()
    if not rows:
        return None
    names = [r["name"] for r in rows]
    views = [r["views"] for r in rows]
    plt.figure(figsize=(8,5))
    plt.barh(range(len(names))[::-1], views[::-1], align='center')
    plt.yticks(range(len(names)), names[::-1], fontsize=8)
    plt.xlabel("Ko‘rilishlar")
    plt.title("Eng ko‘p ko‘rilgan kinolar")
    plt.tight_layout()
    plt.savefig(path)
    plt.close()
    return path

# schedule jobs
scheduler.add_job(backup_db, "cron", hour=0, minute=0)  # daily backup at midnight
# daily news job placeholder (can be enabled by admin)
def daily_news_job():
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT content, caption FROM news WHERE scheduled=1")
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return
    # send each scheduled news to all users
    # note: limit rate to avoid flood
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT id FROM users")
    users = [r["id"] for r in cur.fetchall()]
    cur.close()
    for content_row in rows:
        content = content_row["content"]
        caption = content_row["caption"]
        for uid in users:
            try:
                bot.send_message(uid, content)
            except Exception as e:
                logger.debug("daily_news sending error to %s: %s", uid, e)
scheduler.add_job(daily_news_job, "cron", hour=9, minute=0)  # example: every day at 09:00 UTC

# ---------------- Keyboards ----------------
def main_kb(lang='uz'):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎲 Tasodifiy kino", "🎞 Kinolar")
    kb.add("🔎 Qidiruv", "📢 Kanallar")
    kb.add("⚙️ Sozlamalar", "ℹ️ Yordam")
    return kb

def admin_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("/addmovie", "/delmovie")
    kb.add("/addchannel", "/delchannel")
    kb.add("/news_add", "/news_list")
    kb.add("/topmovies", "/stats")
    kb.add("/give_premium", "/revoke_premium")
    kb.add("/addadmin", "/deladmin")
    return kb

# ---------------- Handlers ----------------

@bot.message_handler(commands=['start'])
def cmd_start(message):
    user = message.from_user
    add_user_if_new(user)
    # referral handling if present
    if message.text and message.text.startswith("/start "):
        parts = message.text.split()
        if len(parts) > 1:
            try:
                ref = int(parts[1])
                if ref != user.id:
                    # set referred_by and increment referrer count
                    c = get_conn(); cur = c.cursor()
                    cur.execute("UPDATE users SET referred_by=? WHERE id=?", (ref, user.id))
                    cur.execute("INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?, ?, ?)",
                                (ref, user.id, datetime.utcnow().isoformat()))
                    cur.execute("UPDATE users SET referrals = referrals + 1 WHERE id=?", (ref,))
                    c.commit(); c.close()
            except:
                pass
    text = f"👋 Assalomu alaykum, {getattr(user,'first_name','')}!\nKinoTreyler ga xush kelibsiz.\nRaqam yuboring yoki menyudan tanlang."
    bot.send_message(message.chat.id, text, reply_markup=main_kb())

@bot.message_handler(commands=['help'])
def cmd_help(message):
    bot.send_message(message.chat.id, "Yordam: kino raqamini yuboring yoki menyudan tanlang.", reply_markup=main_kb())

@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Siz admin emassiz.")
        return
    bot.send_message(message.chat.id, "🔧 Admin panel", reply_markup=admin_kb())

# ----- Add movie flow (admin) -----
admin_states = {}  # user_id -> state dict

@bot.message_handler(commands=['addmovie'])
def cmd_addmovie(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    admin_states[message.from_user.id] = {"action":"await_name"}
    bot.send_message(message.chat.id, "🎬 Kinoning nomini yuboring (to'liq):")

@bot.message_handler(func=lambda m: m.from_user.id in admin_states and admin_states[m.from_user.id]["action"]=="await_name", content_types=['text'])
def _addmovie_name(message):
    state = admin_states[message.from_user.id]
    state['name'] = message.text.strip()
    state['action'] = 'await_desc'
    bot.send_message(message.chat.id, "Kino haqida qisqacha ma'lumot yuboring (janr, yil, tili va hokazo):")

@bot.message_handler(func=lambda m: m.from_user.id in admin_states and admin_states[m.from_user.id]["action"]=="await_desc", content_types=['text'])
def _addmovie_desc(message):
    state = admin_states[message.from_user.id]
    state['desc'] = message.text.strip()
    state['action'] = 'await_file'
    bot.send_message(message.chat.id, "Endi video yoki fayl yuboring (video/document/animation):")

@bot.message_handler(func=lambda m: m.from_user.id in admin_states and admin_states[m.from_user.id]["action"]=="await_file", content_types=['video','document','animation','audio','photo'])
def _addmovie_file(message):
    uid = message.from_user.id
    state = admin_states.get(uid, {})
    file_id = None
    # get file_id depending on type
    if message.content_type == 'video' and message.video:
        file_id = message.video.file_id
    elif message.content_type == 'animation' and message.animation:
        file_id = message.animation.file_id
    elif message.content_type == 'document' and message.document:
        file_id = message.document.file_id
    elif message.content_type == 'audio' and message.audio:
        file_id = message.audio.file_id
    elif message.content_type == 'photo' and message.photo:
        file_id = message.photo[-1].file_id
    if not file_id:
        bot.send_message(message.chat.id, "❗ Video yoki fayl topilmadi. Jarayon bekor qilindi.")
        admin_states.pop(uid, None)
        return
    state['file_id'] = file_id
    state['action'] = 'await_genre'
    bot.send_message(message.chat.id, "Janrini yozing (masalan: jangari, komediya):")

@bot.message_handler(func=lambda m: m.from_user.id in admin_states and admin_states[m.from_user.id]["action"]=="await_genre", content_types=['text'])
def _addmovie_genre(message):
    uid = message.from_user.id
    state = admin_states.get(uid, {})
    state['genre'] = message.text.strip()
    state['action'] = 'await_premium'
    bot.send_message(message.chat.id, "Agar bu kino PREMIUM bo'lsa 'ha' deb yozing, aks holda 'yoq' yoki 'yo‘q':")

@bot.message_handler(func=lambda m: m.from_user.id in admin_states and admin_states[m.from_user.id]["action"]=="await_premium", content_types=['text'])
def _addmovie_premium(message):
    uid = message.from_user.id
    state = admin_states.pop(uid, None)
    if not state:
        bot.send_message(message.chat.id, "Jarayon xatolik bilan yakunlandi.")
        return
    ans = message.text.strip().lower()
    premium = 1 if ans in ("ha","yes","y") else 0
    mid = add_movie(state['name'], state.get('desc',''), state['file_id'], state.get('genre'), premium, added_by=uid)
    bot.send_message(message.chat.id, f"✅ Kino qo'shildi. ID: {mid}\nNom: {state['name']}\nJanr: {state.get('genre')}\nPremium: {'Ha' if premium else 'Yo‘q'}")

# ----- Delete movie (admin) -----
@bot.message_handler(commands=['delmovie'])
def cmd_delmovie(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    parts = message.text.split()
    if len(parts) < 2:
        # list movies with ids
        ms = list_movies(limit=100)
        if not ms:
            bot.send_message(message.chat.id, "🎞 Bazada kino mavjud emas.")
            return
        txt = "📋 Kinolar ro'yxati:\n"
        for m in ms:
            txt += f"{m['id']}. {m['name']}\n"
        txt += "\nO'chirish uchun: /delmovie ID"
        bot.send_message(message.chat.id, txt)
        return
    try:
        mid = int(parts[1])
    except:
        bot.send_message(message.chat.id, "ID noto'g'ri.")
        return
    cnt = delete_movie(mid)
    bot.send_message(message.chat.id, f"✅ O'chirildi: {cnt} qator.")    

# ----- List movies, show a movie by id (user) -----
@bot.message_handler(func=lambda m: m.text and m.text.strip().isdigit())
def send_movie_by_number(message):
    try:
        mid = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "Noto'g'ri raqam.")
        return
    movie = get_movie(mid)
    if not movie:
        bot.send_message(message.chat.id, "🎞 Bunday kino topilmadi.")
        return
    # check subscription to channels
    ok, missing = user_subscribed_all(message.from_user.id)
    if not ok:
        txt = "📢 Majburiy kanallarga a'zo bo'ling:\n" + "\n".join(missing)
        bot.send_message(message.chat.id, txt)
        return
    # if premium movie check
    if movie.get('premium') and not is_premium_user(message.from_user.id):
        bot.send_message(message.chat.id, "🔒 Bu kino faqat PREMIUM foydalanuvchilar uchun.")
        return
    # send movie (as file_id)
    try:
        bot.send_chat_action(message.chat.id, "upload_video")
        bot.send_video(message.chat.id, movie['file_id'], caption=f"🎬 {movie['name']}\n\n{movie.get('description','')}")
        inc_view(mid)
    except Exception as e:
        logger.exception("send movie error: %s", e)
        bot.send_message(message.chat.id, f"Xatolik yuz berdi: {e}")

# ----- Random movie -----
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower() in ("🎲 tasodifiy kino","/random","/randommovie"))
def cmd_random(message):
    ms = list_movies()
    if not ms:
        bot.send_message(message.chat.id, "🎞 Bazada kino yo'q.")
        return
    m = random.choice(ms)
    # subscription check
    ok, missing = user_subscribed_all(message.from_user.id)
    if not ok:
        bot.send_message(message.chat.id, "📢 Iltimos majburiy kanallarga a'zo bo'ling:\n" + "\n".join(missing))
        return
    try:
        bot.send_video(message.chat.id, m['file_id'], caption=f"🎬 {m['name']}\n\n{m.get('description','')}")
        inc_view(m['id'])
    except Exception as e:
        logger.exception("random send error: %s", e)
        bot.send_message(message.chat.id, "Xatolik yuz berdi.")

# ----- Search -----
@bot.message_handler(func=lambda m: m.text and m.text.strip().lower().startswith("/qidir"))
def cmd_search(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Foydalanish: /qidir <so'z>")
        return
    q = parts[1].strip()
    res = search_movies(q)
    if not res:
        bot.send_message(message.chat.id, "Natija topilmadi.")
        return
    txt = "🔎 Qidiruv natijalari:\n"
    for r in res:
        txt += f"{r['id']}. {r['name']} ({r.get('genre','')})\n"
    bot.send_message(message.chat.id, txt)

# ----- Channels management (admin) -----
@bot.message_handler(commands=['addchannel'])
def cmd_addchannel(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Foydalanish: /addchannel @channelusername")
        return
    ch = parts[1].strip()
    ok = add_channel(ch)
    bot.send_message(message.chat.id, "✅ Kanal qo'shildi." if ok else "⚠ Kanal avvaldan mavjud yoki nom noto'g'ri.")

@bot.message_handler(commands=['delchannel'])
def cmd_delchannel(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Foydalanish: /delchannel @channelusername")
        return
    ch = parts[1].strip()
    cnt = remove_channel(ch)
    bot.send_message(message.chat.id, f"✅ O'chirildi: {cnt} qator.")

@bot.message_handler(commands=['channels'])
def cmd_channels(message):
    chs = list_channels()
    if not chs:
        bot.send_message(message.chat.id, "Majburiy kanallar ro'yxati bo'sh.")
        return
    bot.send_message(message.chat.id, "📢 Majburiy kanallar:\n" + "\n".join(chs))

# ----- Stats & admin features -----
@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    c = get_conn()
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM users")
    users = cur.fetchone()["cnt"]
    cur.execute("SELECT COUNT(*) as cnt FROM movies")
    movies = cur.fetchone()["cnt"]
    c.close()
    bot.send_message(message.chat.id, f"📊 Foydalanuvchilar: {users}\n🎞 Kinolar: {movies}")

@bot.message_handler(commands=['topmovies'])
def cmd_topmovies(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    path = generate_stats_plot()
    if path:
        bot.send_photo(message.chat.id, open(path, 'rb'))
    else:
        bot.send_message(message.chat.id, "Hozircha statistika mavjud emas.")

# ----- Give/revoke premium -----
def is_premium_user(uid:int) -> bool:
    c = get_conn(); cur = c.cursor()
    cur.execute("SELECT is_premium FROM users WHERE id=?", (uid,))
    r = cur.fetchone()
    c.close()
    return bool(r and r["is_premium"])

@bot.message_handler(commands=['give_premium'])
def cmd_give_premium(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    parts = message.text.split()
    if len(parts)<2:
        bot.send_message(message.chat.id, "Foydalanish: /give_premium <user_id>")
        return
    try:
        uid = int(parts[1]); c = get_conn(); cur = c.cursor()
        cur.execute("INSERT OR IGNORE INTO users (id, joined_at) VALUES (?, ?)", (uid, datetime.utcnow().isoformat()))
        cur.execute("UPDATE users SET is_premium=1 WHERE id=?", (uid,))
        c.commit(); c.close()
        bot.send_message(message.chat.id, f"✅ {uid} premium qilindi.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Xatolik: {e}")

@bot.message_handler(commands=['revoke_premium'])
def cmd_revoke_premium(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    parts = message.text.split()
    if len(parts)<2:
        bot.send_message(message.chat.id, "Foydalanish: /revoke_premium <user_id>")
        return
    try:
        uid = int(parts[1]); c = get_conn(); cur = c.cursor()
        cur.execute("UPDATE users SET is_premium=0 WHERE id=?", (uid,))
        c.commit(); c.close()
        bot.send_message(message.chat.id, f"✅ {uid} premium bekor qilindi.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Xatolik: {e}")

# ----- News broadcast (admin) -----
@bot.message_handler(commands=['news_add'])
def cmd_news_add(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    # admin will send "kind|caption|content" in next message for simplicity
    msg = bot.reply_to(message, "Yuboring: <kind>|<caption>|<content>\nkind: text/photo/video\nMisol: text|Sarlavha|Matn xabari")
    bot.register_next_step_handler(msg, _save_news)

def _save_news(message):
    try:
        parts = message.text.split("|", 2)
        if len(parts) < 3:
            bot.send_message(message.chat.id, "Format noto'g'ri.")
            return
        kind, caption, content = parts[0].strip(), parts[1].strip(), parts[2].strip()
        c = get_conn(); cur = c.cursor()
        cur.execute("INSERT INTO news (kind, caption, content, created_at) VALUES (?, ?, ?, ?)",
                    (kind, caption, content, datetime.utcnow().isoformat()))
        c.commit(); c.close()
        bot.send_message(message.chat.id, "✅ Yangilik qo'shildi.")
    except Exception as e:
        bot.send_message(message.chat.id, f"Xatolik: {e}")

@bot.message_handler(commands=['news_list'])
def cmd_news_list(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    c = get_conn(); cur = c.cursor()
    cur.execute("SELECT id, kind, caption, created_at FROM news ORDER BY id DESC LIMIT 50")
    rows = cur.fetchall()
    c.close()
    if not rows:
        bot.send_message(message.chat.id, "Yangilik mavjud emas.")
        return
    txt = "🗞 Yangiliklar:\n"
    for r in rows:
        txt += f"{r['id']}. [{r['kind']}] {r['caption']} ({r['created_at']})\n"
    bot.send_message(message.chat.id, txt)

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(message):
    if not is_admin(message.from_user.id):
        bot.send_message(message.chat.id, "⛔ Faqat adminlar.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Foydalanish: /broadcast <xabar matni>")
        return
    text = parts[1]
    c = get_conn(); cur = c.cursor()
    cur.execute("SELECT id FROM users")
    users = [r["id"] for r in cur.fetchall()]
    c.close()
    sent = 0
    for uid in users:
        try:
            bot.send_message(uid, text)
            sent += 1
        except Exception:
            continue
    bot.send_message(message.chat.id, f"Jo'natildi: {sent}/{len(users)}")

# ----- Admin management -----
@bot.message_handler(commands=['addadmin'])
def cmd_addadmin(message):
    if message.from_user.id != MAIN_ADMIN_ID:
        bot.send_message(message.chat.id, "Fa olgina asosiy admin buyrug'ini bajaradi.")
        return
    parts = message.text.split()
    if len(parts)<2:
        bot.send_message(message.chat.id, "Foydalanish: /addadmin <user_id>")
        return
    try:
        uid = int(parts[1]); add_admin(uid)
        bot.send_message(message.chat.id, f"✅ Admin qo'shildi: {uid}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Xatolik: {e}")

@bot.message_handler(commands=['deladmin'])
def cmd_deladmin(message):
    if message.from_user.id != MAIN_ADMIN_ID:
        bot.send_message(message.chat.id, "Fa olgina asosiy admin buyrug'ini bajaradi.")
        return
    parts = message.text.split()
    if len(parts)<2:
        bot.send_message(message.chat.id, "Foydalanish: /deladmin <user_id>")
        return
    try:
        uid = int(parts[1]); remove_admin(uid)
        bot.send_message(message.chat.id, f"✅ Admin o'chirildi: {uid}")
    except Exception as e:
        bot.send_message(message.chat.id, f"Xatolik: {e}")

# ----- Misc: help, channels info -----
@bot.message_handler(commands=['help_cmd'])
def cmd_help_cmd(message):
    txt = (
        "/start - asosiy menyu\n"
        "/addmovie - kino qo'shish (admin)\n"
        "/delmovie - kino o'chirish (admin)\n"
        "/addchannel @name - majburiy kanal qo'shish (admin)\n"
        "/delchannel @name - majburiy kanal o'chirish (admin)\n"
        "/channels - majburiy kanallar\n"
        "/random - tasodifiy kino\n"
        "/qidir <so'z> - qidiruv\n"
        "/stats - foydalanuvchi va kino statistikasi (admin)\n"
        "/topmovies - grafik (admin)\n"
    )
    bot.send_message(message.chat.id, txt)

# Webhook handler
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
    return "<b>KinoTreyler — running</b>"

def set_webhook():
    if not WEBHOOK_URL:
        logger.warning("WEBHOOK_URL not set.")
        return False
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
        logger.exception("Failed to set webhook: %s", e)
        return False

if __name__ == "__main__":
    init_db()
    ok = set_webhook()
    logger.info("Webhook set: %s", ok)
    app.run(host="0.0.0.0", port=PORT)
