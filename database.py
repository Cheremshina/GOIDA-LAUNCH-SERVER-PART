import sqlite3
import uuid
import time
from datetime import datetime
import os

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

def load_admins():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT telegram_id FROM admins')
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]