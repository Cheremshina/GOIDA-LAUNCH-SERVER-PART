import sqlite3
import uuid
import hashlib
import secrets
import time
import logging
import os
import sys
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import threading
import asyncio
import requests as req
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ---------- Настройка логирования ----------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Конфигурация ----------
TELEGRAM_TOKEN = "8649174136:AAFX3ZEp48GWRJuaQQyn8RRGKYu5iIqIdns"  # замените на ваш токен
ADMIN_IDS = []

app = Flask(__name__)
CORS(app)

# ---------- База данных ----------
def init_db():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        telegram_id INTEGER UNIQUE,
        uuid TEXT UNIQUE,
        role TEXT DEFAULT 'player',
        ban_expires REAL,
        ban_reason TEXT,
        created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins (
        telegram_id INTEGER PRIMARY KEY
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS online_clients (
        username TEXT PRIMARY KEY,
        last_seen REAL,
        ip TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS auth_codes (
        username TEXT,
        code TEXT,
        expires_at REAL,
        PRIMARY KEY (username, code)
    )''')
    conn.commit()
    conn.close()

init_db()

def load_admins():
    global ADMIN_IDS
    # Захардкоженный админ
    hardcoded = 5287355502
    ADMIN_IDS = [hardcoded]
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT telegram_id FROM admins')
    rows = c.fetchall()
    for row in rows:
        if row[0] not in ADMIN_IDS:
            ADMIN_IDS.append(row[0])
    conn.close()

load_admins()

# ---------- Работа с пользователями ----------
def get_user_by_username(username):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT username, telegram_id, uuid, role, ban_expires, ban_reason FROM users WHERE username = ?', (username,))
    row = c.fetchone()
    conn.close()
    return row

def create_user(username, telegram_id):
    user_uuid = str(uuid.uuid4())
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users (username, telegram_id, uuid, role, created_at) VALUES (?, ?, ?, ?, ?)',
                  (username, telegram_id, user_uuid, 'player', datetime.now().isoformat()))
        conn.commit()
        return user_uuid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def update_user_role(username, role, ban_expires=None, ban_reason=None):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('UPDATE users SET role = ?, ban_expires = ?, ban_reason = ? WHERE username = ?',
              (role, ban_expires, ban_reason, username))
    conn.commit()
    conn.close()

def is_user_banned(username):
    user = get_user_by_username(username)
    if not user:
        return False, None
    role = user[3]
    ban_expires = user[4]
    ban_reason = user[5]
    if role == 'banned':
        if ban_expires is None or time.time() < ban_expires:
            return True, ban_reason
        else:
            update_user_role(username, 'player', None, None)
            return False, None
    return False, None

def store_code(username, code):
    expires = time.time() + 120
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO auth_codes (username, code, expires_at) VALUES (?, ?, ?)',
              (username, code, expires))
    conn.commit()
    conn.close()

def verify_code(username, code):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('SELECT expires_at FROM auth_codes WHERE username = ? AND code = ?', (username, code))
    row = c.fetchone()
    conn.close()
    if row is None or time.time() > row[0]:
        return False
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('DELETE FROM auth_codes WHERE username = ? AND code = ?', (username, code))
    conn.commit()
    conn.close()
    return True

def update_online(username, ip):
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO online_clients (username, last_seen, ip) VALUES (?, ?, ?)',
              (username, time.time(), ip))
    conn.commit()
    conn.close()

def get_online_list():
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    cutoff = time.time() - 120
    c.execute('DELETE FROM online_clients WHERE last_seen < ?', (cutoff,))
    conn.commit()
    c.execute('SELECT username, ip, last_seen FROM online_clients ORDER BY username')
    rows = c.fetchall()
    conn.close()
    return rows

def is_admin(telegram_id):
    return telegram_id in ADMIN_IDS

# ---------- Обработчики бота ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Привет! Я бот для входа в GoidaCraft.\n"
        "Чтобы привязать Telegram к игровому нику, отправь команду:\n"
        "/bind <твой_ник>\n\n"
        "Пример: /bind misha_6776"
    )

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    await update.message.reply_text(
        f"🆔 Ваш Telegram ID: `{user_id}`\n"
        f"👤 Ваш @username: @{username}",
        parse_mode='Markdown'
    )

async def bind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    parts = text.split()
    if len(parts) < 2:
        await update.message.reply_text("❌ Укажи ник после команды. Пример: /bind misha_6776")
        return
    username = parts[1].strip()
    telegram_id = update.effective_user.id
    logger.info(f"Bind attempt: username={username}, telegram_id={telegram_id}")

    existing = get_user_by_username(username)
    if existing:
        if existing[1] is not None:
            await update.message.reply_text("❌ Этот ник уже привязан к другому Telegram‑аккаунту.")
            return
        else:
            conn = sqlite3.connect('users.db')
            c = conn.cursor()
            c.execute('UPDATE users SET telegram_id = ? WHERE username = ?', (telegram_id, username))
            conn.commit()
            conn.close()
            await update.message.reply_text(f"✅ Аккаунт {username} успешно привязан к Telegram!")
            return
    else:
        user_uuid = create_user(username, telegram_id)
        if user_uuid:
            await update.message.reply_text(
                f"✅ Новый аккаунт {username} создан и привязан к Telegram!\n"
                f"Теперь ты можешь входить в лаунчер, запрашивая код."
            )
        else:
            await update.message.reply_text("❌ Ошибка создания аккаунта. Возможно, ник уже занят.")

async def setrole(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("❌ Использование: /setrole <ник> <роль> [срок_бана_в_секундах]")
        return
    username = args[0]
    role = args[1].lower()
    ban_expires = None
    ban_reason = None
    if role == 'banned':
        if len(args) >= 3:
            try:
                seconds = int(args[2])
                ban_expires = time.time() + seconds
                ban_reason = ' '.join(args[3:]) if len(args) > 3 else 'Нарушение правил'
            except:
                await update.message.reply_text("❌ Неверный формат времени (секунды).")
                return
        else:
            ban_expires = None
            ban_reason = 'Бессрочный бан'
    user = get_user_by_username(username)
    if not user:
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    update_user_role(username, role, ban_expires, ban_reason)
    await update.message.reply_text(f"✅ Роль {username} изменена на {role}.")

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ Использование: /ban <ник> [причина]")
        return
    username = args[0]
    reason = ' '.join(args[1:]) if len(args) > 1 else 'Нарушение правил'
    user = get_user_by_username(username)
    if not user:
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    update_user_role(username, 'banned', None, reason)
    await update.message.reply_text(f"✅ Игрок {username} забанен. Причина: {reason}")

async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ Использование: /unban <ник>")
        return
    username = args[0]
    user = get_user_by_username(username)
    if not user:
        await update.message.reply_text("❌ Пользователь не найден.")
        return
    update_user_role(username, 'player', None, None)
    await update.message.reply_text(f"✅ Игрок {username} разбанен.")

async def online(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    clients = get_online_list()
    if not clients:
        await update.message.reply_text("📭 В данный момент никто не онлайн.")
        return
    msg = "🟢 Онлайн клиенты:\n"
    for username, ip, last_seen in clients:
        msg += f"• {username} (IP: {ip}, last ping: {datetime.fromtimestamp(last_seen).strftime('%H:%M:%S')})\n"
    await update.message.reply_text(msg)

async def shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    await update.message.reply_text("🛑 Сервер и бот отключаются...")
    logger.info("Shutdown initiated by admin.")
    os._exit(0)

async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("❌ Использование: /addadmin <telegram_id>")
        return
    try:
        new_admin_id = int(args[0])
    except:
        await update.message.reply_text("❌ Telegram ID должен быть числом.")
        return
    conn = sqlite3.connect('users.db')
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)', (new_admin_id,))
    conn.commit()
    conn.close()
    load_admins()
    await update.message.reply_text(f"✅ Администратор {new_admin_id} добавлен.")

# ---------- Создание бота ----------
application = Application.builder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("getid", getid))
application.add_handler(CommandHandler("bind", bind))
application.add_handler(CommandHandler("setrole", setrole))
application.add_handler(CommandHandler("ban", ban))
application.add_handler(CommandHandler("unban", unban))
application.add_handler(CommandHandler("online", online))
application.add_handler(CommandHandler("shutdown", shutdown))
application.add_handler(CommandHandler("addadmin", addadmin))

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(application.run_polling())
    except Exception as e:
        logger.error(f"Bot polling error: {e}")
    finally:
        loop.close()

thread = threading.Thread(target=run_bot, daemon=True)
thread.start()

# ---------- Flask endpoints ----------
@app.route('/api/request_code', methods=['POST'])
def request_code():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Нет данных'}), 400
    username = data.get('username')
    if not username:
        return jsonify({'success': False, 'error': 'Укажите имя пользователя'}), 400
    user = get_user_by_username(username)
    if not user:
        return jsonify({'success': False, 'error': 'Пользователь не найден'}), 404
    telegram_id = user[1]
    if not telegram_id:
        return jsonify({'success': False, 'error': 'Telegram не привязан. Используйте /bind в боте.'}), 400
    code = str(secrets.randbelow(900000) + 100000)
    store_code(username, code)
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            'chat_id': telegram_id,
            'text': f"🔐 Ваш код для входа в GoidaCraft: `{code}`\n\nКод действителен 2 минуты.",
            'parse_mode': 'Markdown'
        }
        resp = req.post(url, json=payload, timeout=5)
        if resp.status_code != 200:
            logger.error(f"Telegram API error: {resp.text}")
            return jsonify({'success': False, 'error': 'Ошибка отправки кода в Telegram'}), 500
    except Exception as e:
        logger.error(f"Send code exception: {e}")
        return jsonify({'success': False, 'error': 'Не удалось отправить код'}), 500
    return jsonify({'success': True, 'message': 'Код отправлен в Telegram'})

@app.route('/api/verify_code', methods=['POST'])
def verify():
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'Нет данных'}), 400
    username = data.get('username')
    code = data.get('code')
    if not username or not code:
        return jsonify({'success': False, 'error': 'Заполните все поля'}), 400
    if verify_code(username, code):
        user = get_user_by_username(username)
        if not user:
            return jsonify({'success': False, 'error': 'Пользователь не найден'}), 404
        banned, reason = is_user_banned(username)
        if banned:
            return jsonify({'success': False, 'error': f'Вы забанены. Причина: {reason}'}), 403
        token = hashlib.sha256(f"{username}{user[2]}{time.time()}".encode()).hexdigest()
        return jsonify({'success': True, 'token': token, 'uuid': user[2], 'role': user[3]})
    else:
        return jsonify({'success': False, 'error': 'Неверный или просроченный код'}), 401

@app.route('/api/check_status', methods=['POST'])
def check_status():
    data = request.get_json()
    username = data.get('username')
    token = data.get('token')
    if not username or not token:
        return jsonify({'success': False, 'error': 'Не хватает данных'}), 400
    user = get_user_by_username(username)
    if not user:
        return jsonify({'success': False, 'error': 'Пользователь не найден'}), 404
    banned, reason = is_user_banned(username)
    return jsonify({
        'success': True,
        'username': username,
        'role': user[3],
        'uuid': user[2],
        'banned': banned,
        'ban_reason': reason
    })

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    data = request.get_json()
    username = data.get('username')
    token = data.get('token')
    if not username or not token:
        return jsonify({'success': False}), 400
    ip = request.remote_addr
    update_online(username, ip)
    return jsonify({'success': True})

@app.route('/api/online_list', methods=['GET'])
def online_list():
    clients = get_online_list()
    return jsonify({'online': [{'username': c[0], 'ip': c[1], 'last_seen': c[2]} for c in clients]})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)