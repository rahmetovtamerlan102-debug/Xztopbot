import os
import sys
import asyncio
import traceback
import uvicorn
import secrets
import sqlite3
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from init_data_py import validate_by_hash, errors

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import aiohttp
from datetime import datetime
from decimal import Decimal, getcontext
import phonenumbers
from phonenumbers import geocoder, carrier
from tonsdk.utils import Address

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
TON_API_KEY = os.environ.get("TON_API_KEY", "")
getcontext().prec = 20

os.makedirs("sessions", exist_ok=True)

# ==========================================
# БАЗА ДАННЫХ (SQLite)
# ==========================================
DB_PATH = "database.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            api_key TEXT,
            balance REAL DEFAULT 5.0
        )
    ''')
    conn.commit()
    conn.close()

def get_user_data(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT api_key, balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"api_key": row[0], "balance": row[1]}
    return None

def create_user(user_id):
    new_key = f"arg_{secrets.token_urlsafe(24)}"
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO users (user_id, api_key, balance) VALUES (?, ?, ?)", (user_id, new_key, 5.0))
    conn.commit()
    conn.close()
    return new_key

def update_balance(user_id, amount):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    conn.close()

init_db()

# ==========================================
# ИНИЦИАЛИЗАЦИЯ
# ==========================================
PRICES = {"user": 0.1, "phone": 0.1, "ton": 0.3, "ip": 0.1}

bot = None
user_app = None

app = FastAPI()
class SearchRequest(BaseModel): 
    query: str
    initData: str
class KeyRequest(BaseModel): initData: str

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ==========================================
# ОЦЕНКА ДАТЫ РЕГИСТРАЦИИ
# ==========================================
MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

def estimate_registration(user_id):
    db = [
        (100000000, 2013, 8), (500000000, 2015, 4), (1000000000, 2016, 4), 
        (2000000000, 2017, 7), (3000000000, 2018, 6), (4000000000, 2019, 4), 
        (5000000000, 2020, 1), (6000000000, 2021, 2), (7000000000, 2022, 6), 
        (8000000000, 2024, 6), (8300000000, 2025, 1), (8600000000, 2025, 7), 
        (8900000000, 2026, 1), (9200000000, 2026, 7),
    ]
    if user_id <= db[0][0]: return f"~ {MONTHS_RU[db[0][2]]} {db[0][1]}"
    if user_id >= db[-1][0]: return f"~ {MONTHS_RU[db[-1][2]]} {db[-1][1]}"
    for i in range(len(db) - 1):
        if db[i][0] <= user_id <= db[i+1][0]:
            id1, y1, m1 = db[i]
            id2, y2, m2 = db[i+1]
            total_months = (y2 - y1) * 12 + (m2 - m1)
            ratio = (user_id - id1) / (id2 - id1)
            add_months = int(ratio * total_months)
            final_month_index = (y1 * 12 + m1 - 1) + add_months
            final_year = final_month_index // 12
            final_month = (final_month_index % 12) + 1
            return f"~ {MONTHS_RU[final_month]} {final_year}"
    return "Неизвестно"

# ==========================================
# АНАЛИЗ TON
# ==========================================
def to_friendly(addr):
    if not addr or addr == "?": return "?"
    try: return Address(addr).to_string(is_user_friendly=True, is_bounceable=False).replace('/', '_').replace('+', '-')
    except: return addr

async def analyze_ton_wallet_full(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"
    try:
        wallet_address = to_friendly(wallet_address)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200: return None, "Кошелек не найден"
                acc = await r.json()
                balance = Decimal(int(acc.get("balance", 0))) / Decimal(10**9)
                status = acc.get("status", "unknown")
            
            dns_name = "Неидентифицированные"
            nft_stats = {"recv": 0, "mint": 0, "sent": 0, "spam": 0, "collections": {}}
            async with session.get(f"{base_url}/accounts/{wallet_address}/nfts?limit=100", headers=headers) as r:
                if r.status == 200:
                    for item in (await r.json()).get("nft_items", []):
                        name = (item.get("metadata") or {}).get("name") or ""
                        if name.endswith(".ton"):
                            dns_name = name
                            continue
                        if item.get("trust") == "blacklist": nft_stats["spam"] += 1
                        else:
                            coll = (item.get("collection") or {}).get("name")
                            if coll: nft_stats["collections"][coll] = nft_stats["collections"].get(coll, 0) + 1

            events = []
            total_in, total_out = Decimal(0), Decimal(0)
            senders_dict, receivers_dict = {}, {}
            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=100", headers=headers) as r:
                if r.status == 200:
                    for tx in (await r.json()).get("transactions", []):
                        utime = tx.get("utime", 0)
                        if tx.get("in_msg") and tx["in_msg"].get("value"):
                            amt = Decimal(int(tx["in_msg"]["value"])) / Decimal(10**9)
                            if amt >= Decimal('0.1'):
                                total_in += amt
                                src = to_friendly(tx["in_msg"].get("source", {}).get("address", "?"))
                                if src != "?":
                                    events.append((utime, "IN", float(amt), src))
                                    if src not in senders_dict: senders_dict[src] = {'sum': Decimal(0), 'last': utime}
                                    senders_dict[src]['sum'] += amt; senders_dict[src]['last'] = utime
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amt = Decimal(int(out["value"])) / Decimal(10**9)
                                if amt >= Decimal('0.1'):
                                    total_out += amt
                                    dst = to_friendly(out.get("destination", {}).get("address", "?"))
                                    if dst != "?":
                                        events.append((utime, "OUT", float(amt), dst))
                                        if dst not in receivers_dict: receivers_dict[dst] = {'sum': Decimal(0), 'last': utime}
                                        receivers_dict[dst]['sum'] += amt; receivers_dict[dst]['last'] = utime
            
            top_senders_list = sorted(senders_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            top_receivers_list = sorted(receivers_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            
            senders_api = [{"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—"} for a, d in top_senders_list]
            receivers_api = [{"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—"} for a, d in top_receivers_list]

            return {
                "balance": f"{balance.normalize():f}", "status": status, "dns": dns_name,
                "total_in": f"{total_in.normalize():f}", "total_out": f"{total_out.normalize():f}",
                "senders": senders_api, "receivers": receivers_api, "nft": nft_stats
            }, None
    except Exception as e:
        return None, str(e)

# ==========================================
# ПОИСК ПО IP-АДРЕСУ
# ==========================================
async def check_ip_osint(ip: str):
    result = {}
    try:
        # Используем бесплатный ip-api.com (лимит 45 запросов в минуту без ключа)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://ip-api.com/json/{ip}?fields=status,message,continent,country,countryCode,region,regionName,city,district,zip,lat,lon,timezone,offset,currency,isp,org,as,asname,reverse,mobile,proxy,hosting,query") as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("status") == "success":
                        result = {
                            "query": data.get("query"),
                            "continent": data.get("continent"),
                            "country": data.get("country"),
                            "region": data.get("regionName"),
                            "city": data.get("city"),
                            "zip": data.get("zip"),
                            "lat": data.get("lat"),
                            "lon": data.get("lon"),
                            "timezone": data.get("timezone"),
                            "isp": data.get("isp"),
                            "org": data.get("org"),
                            "as": data.get("as"),
                            "mobile": data.get("mobile"),
                            "proxy": data.get("proxy"),
                            "hosting": data.get("hosting")
                        }
                    else:
                        return {"error": data.get("message", "IP не найден")}
                else:
                    return {"error": "Ошибка запроса к ip-api.com"}
    except Exception as e:
        return {"error": str(e)}
    
    return result

# ==========================================
# ПОДАРКИ
# ==========================================
async def get_profile_gifts(user_id: int):
    try:
        from pyrogram.raw.functions.payments import GetSavedStarGifts
        peer = await user_app.resolve_peer(user_id)
        result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=20, exclude_unsaved=True))
        if not result or not result.gifts: return "Нет"
        return [{"sender": str(g.from_id.user_id) if getattr(g, 'from_id', None) and hasattr(g.from_id, 'user_id') else "Аноним", "stars": g.gift.stars} for g in result.gifts if g.gift.stars > 0]
    except Exception:
        return "Ошибка"

# ==========================================
# API КЛЮЧИ И БАЛАНС
# ==========================================
def get_user_from_init_data(init_data: str) -> dict:
    if not init_data: raise HTTPException(status_code=401, detail="InitData missing")
    try:
        parsed = validate_by_hash(init_data, BOT_TOKEN)
        return {"id": parsed.user.id}
    except errors.InvalidInitDataError as e:
        raise HTTPException(status_code=403, detail=f"Invalid signature: {e}")

@app.post("/api/get_key")
async def get_api_key(req: KeyRequest):
    user = get_user_from_init_data(req.initData)
    user_id = user["id"]
    
    user_data = get_user_data(user_id)
    if not user_data:
        create_user(user_id)
        user_data = get_user_data(user_id)
        
    return JSONResponse({"key": user_data["api_key"], "balance": user_data["balance"]})

@app.get("/api/protected_data")
async def get_protected_data(x_api_key: str = Header(None)):
    if not x_api_key: raise HTTPException(status_code=401, detail="API Key missing")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, balance FROM users WHERE api_key = ?", (x_api_key,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {"status": "OK", "user_id": row[0], "balance": row[1]}
    raise HTTPException(status_code=403, detail="Invalid API Key")

# ==========================================
# ПОИСК ПО API-КЛЮЧУ (ДЛЯ CURL)
# ==========================================
@app.post("/api/search_by_key")
async def search_by_key(x_api_key: str = Header(None), query: str = Header(None)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="API Key missing")
    if not query:
        raise HTTPException(status_code=400, detail="Query missing")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, balance FROM users WHERE api_key = ?", (x_api_key,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=403, detail="Invalid API Key")
    
    user_id, current_balance = row
    q = query.strip()
    
    # Определяем цену
    price = PRICES["user"]
    if q.startswith(("UQ", "EQ", "0:")): price = PRICES["ton"]
    elif q.startswith("+"): price = PRICES["phone"]
    elif q.replace(".", "").isdigit() and q.count(".") == 3: price = PRICES["ip"]
    
    if current_balance < price:
        raise HTTPException(status_code=402, detail=f"Недостаточно средств. Баланс: ${current_balance:.2f}, нужно: ${price}")
    
    update_balance(user_id, -price)
    
    # Логика поиска
    if q.startswith("@"):
        username = q.replace("@", "")
        try:
            user = await user_app.get_users(username)
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")
            return {
                "type": "Telegram Профиль",
                "result": {
                    "Имя": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто",
                    "Username": f"@{user.username}" if user.username else "Нет",
                    "ID": str(user.id),
                    "Регистрация": estimate_registration(user.id),
                    "Подарки": await get_profile_gifts(user.id)
                }
            }
        except Exception as e:
            update_balance(user_id, price)
            raise HTTPException(status_code=500, detail=str(e))
    
    elif q.startswith(("UQ", "EQ", "0:")):
        data, error = await analyze_ton_wallet_full(q)
        if error:
            update_balance(user_id, price)
            raise HTTPException(status_code=500, detail=error)
        return {
            "type": "TON Кошелек",
            "result": {"Баланс": f"{data['balance']} TON", "Статус": data['status'], "Всего вход": f"{data['total_in']} TON", "Всего выход": f"{data['total_out']} TON"},
            "senders": data["senders"], "receivers": data["receivers"], "nft": data["nft"]
        }
    
    elif q.startswith("+") and q[1:].isdigit():
        try:
            parsed = phonenumbers.parse(q, "RU")
            operator = carrier.name_for_number(parsed, "ru") or "Неизвестно"
            region = geocoder.description_for_number(parsed, "ru") or "Неизвестно"
            result_data = {"Номер": q.replace("+", ""), "Оператор": operator, "Регион": region, "Telegram": "Не привязан"}
            try:
                user = await user_app.get_users(q)
                if user:
                    result_data["Telegram"] = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
                    result_data["Username"] = f"@{user.username}" if user.username else "Нет"
                    result_data["ID"] = str(user.id)
                    result_data["Регистрация"] = estimate_registration(user.id)
                    result_data["Подарки"] = await get_profile_gifts(user.id)
            except Exception as e:
                pass
            return {"type": "Телефон", "result": result_data}
        except Exception as e:
            update_balance(user_id, price)
            raise HTTPException(status_code=500, detail=str(e))
    
    elif q.replace(".", "").isdigit() and q.count(".") == 3:
        data = await check_ip_osint(q)
        if "error" in data:
            update_balance(user_id, price) # Возврат при ошибке
            raise HTTPException(status_code=500, detail=data["error"])
        return {"type": "IP (OSINT)", "result": data}
    
    else:
        raise HTTPException(status_code=400, detail="Неизвестный формат запроса")

# ==========================================
# ПОИСК С ТАРИФИКАЦИЕЙ (ДЛЯ MINI APP)
# ==========================================
@app.post("/api/search")
async def handle_search(req: SearchRequest):
    user = get_user_from_init_data(req.initData)
    user_id = user["id"]
    query = req.query.strip()
    
    user_data = get_user_data(user_id)
    if not user_data:
        return JSONResponse({"error": "Пользователь не найден. Откройте профиль."}, status_code=401)

    current_balance = user_data["balance"]
    price = PRICES["user"]
    if query.startswith(("UQ", "EQ", "0:")) or query.endswith(".ton"): price = PRICES["ton"]
    elif query.startswith("+") and query[1:].isdigit(): price = PRICES["phone"]
    elif query.replace(".", "").isdigit() and query.count(".") == 3: price = PRICES["ip"]

    if current_balance < price:
        return JSONResponse({"error": f"Недостаточно средств. Баланс: ${current_balance:.2f}, нужно: ${price}"}, status_code=402)

    update_balance(user_id, -price)

    if query.startswith(("UQ", "EQ", "0:")):
        data, error = await analyze_ton_wallet_full(query)
        if error: 
            update_balance(user_id, price)
            return JSONResponse({"error": error})
        return JSONResponse({
            "type": "TON Кошелек", 
            "result": {"Баланс": f"{data['balance']} TON", "Статус": data['status'], "Всего вход": f"{data['total_in']} TON", "Всего выход": f"{data['total_out']} TON"},
            "senders": data["senders"], "receivers": data["receivers"], "nft": data["nft"]
        })

    elif query.startswith("+") and query[1:].isdigit():
        try:
            parsed = phonenumbers.parse(query, "RU")
            operator = carrier.name_for_number(parsed, "ru") or "Неизвестно"
            region = geocoder.description_for_number(parsed, "ru") or "Неизвестно"
            result_data = {"Номер": query.replace("+", ""), "Оператор": operator, "Регион": region, "Telegram": "Не привязан"}
            try:
                user = await user_app.get_users(query)
                if user:
                    result_data["Telegram"] = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
                    result_data["Username"] = f"@{user.username}" if user.username else "Нет"
                    result_data["ID"] = str(user.id)
                    result_data["Регистрация"] = estimate_registration(user.id)
                    result_data["Подарки"] = await get_profile_gifts(user.id)
            except Exception as e:
                pass
            return JSONResponse({"type": "Телефон", "result": result_data})
        except Exception as e: 
            update_balance(user_id, price)
            return JSONResponse({"error": f"Ошибка: {str(e)}"})

    elif query.replace(".", "").isdigit() and query.count(".") == 3:
        data = await check_ip_osint(query)
        if "error" in data:
            update_balance(user_id, price)
            return JSONResponse({"error": data["error"]})
        return JSONResponse({"type": "IP (OSINT)", "result": data})

    elif query.startswith("@") or "t.me/" in query or query.isdigit():
        username = query.replace("https://", "").replace("http://", "").replace("t.me/", "").replace("@", "")
        try:
            user = await user_app.get_users(int(username) if username.isdigit() else username)
            if not user: return JSONResponse({"error": "Пользователь не найден"})
            result_data = {
                "Имя": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто",
                "Username": f"@{user.username}" if user.username else "Нет",
                "ID": str(user.id),
                "Регистрация": estimate_registration(user.id),
                "Подарки": await get_profile_gifts(user.id)
            }
            return JSONResponse({"type": "Telegram Профиль", "result": result_data})
        except Exception as e: 
            return JSONResponse({"error": f"Ошибка: {str(e)}"})

    return JSONResponse({"error": "Неизвестный формат"})

# ==========================================
# ЗАПУСК
# ==========================================
async def main():
    global bot, user_app
    bot = Client("sessions/my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=60)
    user_app = Client("sessions/my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, no_updates=True, sleep_threshold=60)
    
    @bot.on_message(filters.command("start") & filters.private)
    async def start_command(client, message):
        url = os.environ.get("RENDER_EXTERNAL_URL", "https://ваш-сайт.onrender.com")
        await message.reply("👋 Открой поиск:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Поиск", web_app=WebAppInfo(url=url))]]))

    print("🔌 Подключаю user_app...", flush=True)
    await user_app.start()
    print("🤖 Запускаю бота...", flush=True)
    await bot.start()
    print("✅ Бот запущен!", flush=True)

    port = int(os.environ.get("PORT", 10000))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
