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
import requests as req
from telegram import Bot, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ---------- Настройка логирования ----------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ---------- Конфигурация ----------
TELEGRAM_TOKEN = "8649174136:AAFX3ZEp48GWRJuaQQyn8RRGKYu5iIqIdns"  # замените на ваш токен
ADMIN_IDS = []

app = Flask(__name__)
CORS(app)

# ---------- База данных ----------
HOME = os.path.expanduser("~")
DB_PATH = os.path.join(HOME, "goida_users.db")
os.makedirs(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else HOME, exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    c.execute('''CREATE TABLE IF NOT EXISTS builds (
        name TEXT PRIMARY KEY,
        url TEXT,
        version TEXT,
        hash TEXT,
        updated_at TEXT
    )''')
    conn.commit()
    conn.close()

init_db()

def load_admins():
    global ADMIN_IDS
    hardcoded = 5287355502
    ADMIN_IDS = [hardcoded]
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT username, telegram_id, uuid, role, ban_expires, ban_reason FROM users WHERE username = ?', (username,))
    row = c.fetchone()
    conn.close()
    return row

def create_user(username, telegram_id):
    user_uuid = str(uuid.uuid4())
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO auth_codes (username, code, expires_at) VALUES (?, ?, ?)',
              (username, code, expires))
    conn.commit()
    conn.close()

def verify_code(username, code):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT expires_at FROM auth_codes WHERE username = ? AND code = ?', (username, code))
    row = c.fetchone()
    conn.close()
    if row is None or time.time() > row[0]:
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM auth_codes WHERE username = ? AND code = ?', (username, code))
    conn.commit()
    conn.close()
    return True

def update_online(username, ip):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO online_clients (username, last_seen, ip) VALUES (?, ?, ?)',
              (username, time.time(), ip))
    conn.commit()
    conn.close()

def get_online_list():
    conn = sqlite3.connect(DB_PATH)
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

# ---------- Функции для работы со сборками ----------
def get_build(name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT name, url, version, hash, updated_at FROM builds WHERE name = ?', (name,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'name': row[0], 'url': row[1], 'version': row[2], 'hash': row[3], 'updated_at': row[4]}
    return None

def set_build(name, url, version, hash_val=None):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO builds (name, url, version, hash, updated_at) VALUES (?, ?, ?, ?, ?)',
              (name, url, version, hash_val, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def delete_build(name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM builds WHERE name = ?', (name,))
    conn.commit()
    conn.close()

def list_builds():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT name, version, updated_at FROM builds ORDER BY name')
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- Обработчики команд (синхронные) ----------
def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "👋 Привет! Я бот для входа в GoidaCraft.\n"
        "Чтобы привязать Telegram к игровому нику, отправь команду:\n"
        "/bind <твой_ник>\n\n"
        "Пример: /bind misha_6776"
    )

def getid(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    username = update.effective_user.username or "без username"
    update.message.reply_text(
        f"🆔 Ваш Telegram ID: `{user_id}`\n"
        f"👤 Ваш @username: @{username}",
        parse_mode='Markdown'
    )

def bind(update: Update, context: CallbackContext):
    text = update.message.text
    parts = text.split()
    if len(parts) < 2:
        update.message.reply_text("❌ Укажи ник после команды. Пример: /bind misha_6776")
        return
    username = parts[1].strip()
    telegram_id = update.effective_user.id
    logger.info(f"Bind attempt: username={username}, telegram_id={telegram_id}")

    existing = get_user_by_username(username)
    if existing:
        if existing[1] is not None:
            update.message.reply_text("❌ Этот ник уже привязан к другому Telegram‑аккаунту.")
            return
        else:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('UPDATE users SET telegram_id = ? WHERE username = ?', (telegram_id, username))
            conn.commit()
            conn.close()
            update.message.reply_text(f"✅ Аккаунт {username} успешно привязан к Telegram!")
            return
    else:
        user_uuid = create_user(username, telegram_id)
        if user_uuid:
            update.message.reply_text(
                f"✅ Новый аккаунт {username} создан и привязан к Telegram!\n"
                f"Теперь ты можешь входить в лаунчер, запрашивая код."
            )
        else:
            update.message.reply_text("❌ Ошибка создания аккаунта. Возможно, ник уже занят.")

def setrole(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 2:
        update.message.reply_text("❌ Использование: /setrole <ник> <роль> [срок_бана_в_секундах]")
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
                update.message.reply_text("❌ Неверный формат времени (секунды).")
                return
        else:
            ban_expires = None
            ban_reason = 'Бессрочный бан'
    user = get_user_by_username(username)
    if not user:
        update.message.reply_text("❌ Пользователь не найден.")
        return
    update_user_role(username, role, ban_expires, ban_reason)
    update.message.reply_text(f"✅ Роль {username} изменена на {role}.")

def ban(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 1:
        update.message.reply_text("❌ Использование: /ban <ник> [причина]")
        return
    username = args[0]
    reason = ' '.join(args[1:]) if len(args) > 1 else 'Нарушение правил'
    user = get_user_by_username(username)
    if not user:
        update.message.reply_text("❌ Пользователь не найден.")
        return
    update_user_role(username, 'banned', None, reason)
    update.message.reply_text(f"✅ Игрок {username} забанен. Причина: {reason}")

def unban(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 1:
        update.message.reply_text("❌ Использование: /unban <ник>")
        return
    username = args[0]
    user = get_user_by_username(username)
    if not user:
        update.message.reply_text("❌ Пользователь не найден.")
        return
    update_user_role(username, 'player', None, None)
    update.message.reply_text(f"✅ Игрок {username} разбанен.")

def online(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    clients = get_online_list()
    if not clients:
        update.message.reply_text("📭 В данный момент никто не онлайн.")
        return
    msg = "🟢 Онлайн клиенты:\n"
    for username, ip, last_seen in clients:
        msg += f"• {username} (IP: {ip}, last ping: {datetime.fromtimestamp(last_seen).strftime('%H:%M:%S')})\n"
    update.message.reply_text(msg)

def shutdown(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    update.message.reply_text("🛑 Сервер и бот отключаются...")
    logger.info("Shutdown initiated by admin.")
    os._exit(0)

def addadmin(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 1:
        update.message.reply_text("❌ Использование: /addadmin <telegram_id>")
        return
    try:
        new_admin_id = int(args[0])
    except:
        update.message.reply_text("❌ Telegram ID должен быть числом.")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO admins (telegram_id) VALUES (?)', (new_admin_id,))
    conn.commit()
    conn.close()
    load_admins()
    update.message.reply_text(f"✅ Администратор {new_admin_id} добавлен.")

# ---------- Команды для управления сборками ----------
def addbuild(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 3:
        update.message.reply_text("❌ Использование: /addbuild <название> <url> [версия]")
        return
    name = args[0]
    url = args[1]
    version = args[2] if len(args) > 2 else "1.0"
    set_build(name, url, version, None)
    update.message.reply_text(f"✅ Сборка '{name}' добавлена (URL: {url}, версия: {version})")

def updatebuild(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 3:
        update.message.reply_text("❌ Использование: /updatebuild <название> <новый_url> [новая_версия]")
        return
    name = args[0]
    url = args[1]
    version = args[2] if len(args) > 2 else None
    build = get_build(name)
    if not build:
        update.message.reply_text(f"❌ Сборка '{name}' не найдена.")
        return
    if version is None:
        version = build['version']
    set_build(name, url, version, None)
    update.message.reply_text(f"✅ Сборка '{name}' обновлена (новый URL: {url}, версия: {version})")

def removebuild(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    args = context.args
    if len(args) < 1:
        update.message.reply_text("❌ Использование: /removebuild <название>")
        return
    name = args[0]
    delete_build(name)
    update.message.reply_text(f"✅ Сборка '{name}' удалена.")

def listbuilds(update: Update, context: CallbackContext):
    if not is_admin(update.effective_user.id):
        update.message.reply_text("⛔ У вас нет прав администратора.")
        return
    builds = list_builds()
    if not builds:
        update.message.reply_text("📭 Сборок пока нет.")
        return
    msg = "📦 Доступные сборки:\n"
    for name, version, updated_at in builds:
        msg += f"• {name} (v{version}) — обновлена: {updated_at}\n"
    update.message.reply_text(msg)

# ---------- Создание и запуск бота (синхронный Updater) ----------
def run_bot():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("getid", getid))
    dp.add_handler(CommandHandler("bind", bind))
    dp.add_handler(CommandHandler("setrole", setrole))
    dp.add_handler(CommandHandler("ban", ban))
    dp.add_handler(CommandHandler("unban", unban))
    dp.add_handler(CommandHandler("online", online))
    dp.add_handler(CommandHandler("shutdown", shutdown))
    dp.add_handler(CommandHandler("addadmin", addadmin))
    dp.add_handler(CommandHandler("addbuild", addbuild))
    dp.add_handler(CommandHandler("updatebuild", updatebuild))
    dp.add_handler(CommandHandler("removebuild", removebuild))
    dp.add_handler(CommandHandler("listbuilds", listbuilds))

    updater.start_polling()
    updater.idle()  # блокирует поток до остановки

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
        bot = Bot(token=TELEGRAM_TOKEN)
        bot.send_message(chat_id=telegram_id, text=f"🔐 Ваш код для входа в GoidaCraft: `{code}`\n\nКод действителен 2 минуты.", parse_mode='Markdown')
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

# ---------- API для сборок ----------
@app.route('/api/get_build_info', methods=['GET'])
def get_build_info():
    name = request.args.get('name')
    if not name:
        return jsonify({'success': False, 'error': 'Укажите название сборки'}), 400
    build = get_build(name)
    if not build:
        return jsonify({'success': False, 'error': 'Сборка не найдена'}), 404
    return jsonify({'success': True, 'build': build})

@app.route('/api/list_builds', methods=['GET'])
def list_builds_api():
    builds = list_builds()
    return jsonify({'success': True, 'builds': [{'name': row[0], 'version': row[1]} for row in builds]})

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)