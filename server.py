import os
import time
import json
import logging
import threading
import sqlite3
import requests
import secrets
import hashlib
from flask import Flask, request, jsonify
from flask_cors import CORS
from database import (
    init_db, get_user_by_username, is_user_banned, store_code,
    verify_code, update_online, get_online_list, get_build, list_builds,
    load_admins, create_user, update_user_role, set_build, delete_build
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------- Настройки ----------
app = Flask(__name__)
CORS(app)
init_db()

TOKEN = os.getenv("TELEGRAM_TOKEN", "8649174136:AAFX3ZEp48GWRJuaQQyn8RRGKYu5iIqIdns")
ADMIN_IDS = load_admins()
if 5287355502 not in ADMIN_IDS:
    ADMIN_IDS.append(5287355502)

API_URL = f"https://api.telegram.org/bot{TOKEN}"

# ---------- Flask endpoints (все те же, что были в app.py) ----------
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
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {
            'chat_id': telegram_id,
            'text': f"🔐 Ваш код для входа в GoidaCraft: `{code}`\n\nКод действителен 2 минуты.",
            'parse_mode': 'Markdown'
        }
        resp = requests.post(url, json=payload, timeout=5)
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

# ---------- Функции бота (из bot.py) ----------
def is_admin(telegram_id):
    return telegram_id in ADMIN_IDS

def send_message(chat_id, text, parse_mode=None):
    url = f"{API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        resp = requests.post(url, json=payload, timeout=5)
        return resp.ok
    except Exception as e:
        logger.error(f"Send message error: {e}")
        return False

def get_updates(offset=None):
    url = f"{API_URL}/getUpdates"
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(url, params=params, timeout=35)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result")
    except Exception as e:
        logger.error(f"Get updates error: {e}")
    return []

def process_command(update):
    message = update.get("message")
    if not message:
        return
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "")
    from_user = message.get("from", {})
    user_id = from_user.get("id")

    if not chat_id or not text:
        return

    parts = text.split()
    command = parts[0].lower() if parts else ""

    # Обработка команд (полный набор из bot.py)
    if command == "/start":
        send_message(chat_id, "👋 Привет! Я бот для входа в GoidaCraft.\n"
                              "Чтобы привязать Telegram к игровому нику, отправь команду:\n"
                              "/bind <твой_ник>\n\n"
                              "Пример: /bind misha_6776")
    elif command == "/getid":
        username = from_user.get("username") or "без username"
        send_message(chat_id, f"🆔 Ваш Telegram ID: `{user_id}`\n"
                              f"👤 Ваш @username: @{username}",
                     parse_mode="Markdown")
    elif command == "/bind":
        if len(parts) < 2:
            send_message(chat_id, "❌ Укажи ник после команды. Пример: /bind misha_6776")
            return
        username = parts[1].strip()
        telegram_id = user_id
        logger.info(f"Bind attempt: username={username}, telegram_id={telegram_id}")

        existing = get_user_by_username(username)
        if existing:
            if existing[1] is not None:
                send_message(chat_id, "❌ Этот ник уже привязан к другому Telegram‑аккаунту.")
                return
            else:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('UPDATE users SET telegram_id = ? WHERE username = ?', (telegram_id, username))
                conn.commit()
                conn.close()
                send_message(chat_id, f"✅ Аккаунт {username} успешно привязан к Telegram!")
                return
        else:
            user_uuid = create_user(username, telegram_id)
            if user_uuid:
                send_message(chat_id, f"✅ Новый аккаунт {username} создан и привязан к Telegram!\n"
                                      f"Теперь ты можешь входить в лаунчер, запрашивая код.")
            else:
                send_message(chat_id, "❌ Ошибка создания аккаунта. Возможно, ник уже занят.")
    elif command == "/setrole":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        if len(parts) < 3:
            send_message(chat_id, "❌ Использование: /setrole <ник> <роль> [срок_бана_в_секундах]")
            return
        username = parts[1]
        role = parts[2].lower()
        ban_expires = None
        ban_reason = None
        if role == 'banned':
            if len(parts) >= 4:
                try:
                    seconds = int(parts[3])
                    ban_expires = time.time() + seconds
                    ban_reason = ' '.join(parts[4:]) if len(parts) > 4 else 'Нарушение правил'
                except:
                    send_message(chat_id, "❌ Неверный формат времени (секунды).")
                    return
            else:
                ban_expires = None
                ban_reason = 'Бессрочный бан'
        user = get_user_by_username(username)
        if not user:
            send_message(chat_id, "❌ Пользователь не найден.")
            return
        update_user_role(username, role, ban_expires, ban_reason)
        send_message(chat_id, f"✅ Роль {username} изменена на {role}.")
    elif command == "/ban":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        if len(parts) < 2:
            send_message(chat_id, "❌ Использование: /ban <ник> [причина]")
            return
        username = parts[1]
        reason = ' '.join(parts[2:]) if len(parts) > 2 else 'Нарушение правил'
        user = get_user_by_username(username)
        if not user:
            send_message(chat_id, "❌ Пользователь не найден.")
            return
        update_user_role(username, 'banned', None, reason)
        send_message(chat_id, f"✅ Игрок {username} забанен. Причина: {reason}")
    elif command == "/unban":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        if len(parts) < 2:
            send_message(chat_id, "❌ Использование: /unban <ник>")
            return
        username = parts[1]
        user = get_user_by_username(username)
        if not user:
            send_message(chat_id, "❌ Пользователь не найден.")
            return
        update_user_role(username, 'player', None, None)
        send_message(chat_id, f"✅ Игрок {username} разбанен.")
    elif command == "/online":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        clients = get_online_list()
        if not clients:
            send_message(chat_id, "📭 В данный момент никто не онлайн.")
            return
        msg = "🟢 Онлайн клиенты:\n"
        for username, ip, last_seen in clients:
            msg += f"• {username} (IP: {ip}, last ping: {datetime.fromtimestamp(last_seen).strftime('%H:%M:%S')})\n"
        send_message(chat_id, msg)
    elif command == "/addbuild":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        if len(parts) < 3:
            send_message(chat_id, "❌ Использование: /addbuild <название> <url> [версия]")
            return
        name = parts[1]
        url = parts[2]
        version = parts[3] if len(parts) > 3 else "1.0"
        set_build(name, url, version, None)
        send_message(chat_id, f"✅ Сборка '{name}' добавлена (URL: {url}, версия: {version})")
    elif command == "/updatebuild":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        if len(parts) < 3:
            send_message(chat_id, "❌ Использование: /updatebuild <название> <новый_url> [новая_версия]")
            return
        name = parts[1]
        url = parts[2]
        version = parts[3] if len(parts) > 3 else None
        build = get_build(name)
        if not build:
            send_message(chat_id, f"❌ Сборка '{name}' не найдена.")
            return
        if version is None:
            version = build['version']
        set_build(name, url, version, None)
        send_message(chat_id, f"✅ Сборка '{name}' обновлена (новый URL: {url}, версия: {version})")
    elif command == "/removebuild":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        if len(parts) < 2:
            send_message(chat_id, "❌ Использование: /removebuild <название>")
            return
        name = parts[1]
        delete_build(name)
        send_message(chat_id, f"✅ Сборка '{name}' удалена.")
    elif command == "/listbuilds":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        builds = list_builds()
        if not builds:
            send_message(chat_id, "📭 Сборок пока нет.")
            return
        msg = "📦 Доступные сборки:\n"
        for name, version, updated_at in builds:
            msg += f"• {name} (v{version}) — обновлена: {updated_at}\n"
        send_message(chat_id, msg)
    elif command == "/shutdown":
        if not is_admin(user_id):
            send_message(chat_id, "⛔ У вас нет прав администратора.")
            return
        send_message(chat_id, "🛑 Бот отключается...")
        logger.info("Bot shutdown initiated by admin.")
        os._exit(0)

def bot_loop():
    last_update_id = 0
    logger.info("Бот запущен. Ожидание команд...")
    while True:
        try:
            updates = get_updates(offset=last_update_id + 1)
            for update in updates:
                process_command(update)
                last_update_id = update.get("update_id", last_update_id)
        except Exception as e:
            logger.error(f"Ошибка в основном цикле бота: {e}")
            time.sleep(5)
        time.sleep(1)

# ---------- Запуск ----------
if __name__ == "__main__":
    # Запускаем бота в отдельном потоке
    bot_thread = threading.Thread(target=bot_loop, daemon=True)
    bot_thread.start()
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)