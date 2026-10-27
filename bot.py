import os
import threading
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from pyrogram import Client, filters, raw
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.raw.functions.messages import GetStickerSet
from pyrogram.raw.types import InputStickerSetShortName
from pyrogram.errors import UsernameNotOccupied, PeerIdInvalid, FloodWait, StickersetInvalid
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import aiohttp
import asyncio
import phonenumbers
from phonenumbers import geocoder, carrier
from datetime import datetime, timezone, timedelta
from decimal import Decimal, getcontext
import random
import time
import traceback

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
TON_API_KEY = os.environ.get("TON_API_KEY", "")
getcontext().prec = 20

# ==========================================
# ИНИЦИАЛИЗАЦИЯ КЛИЕНТОВ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)
last_request_time = {}

# ==========================================
# FASTAPI ДЛЯ MINI APP
# ==========================================
app = FastAPI()

class SearchRequest(BaseModel):
    query: str
    initData: str = ""

@app.get("/", response_class=HTMLResponse)
async def read_index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Ошибка: index.html не найден</h1>"

@app.post("/api/search")
async def handle_search(req: SearchRequest):
    query = req.query.strip()
    
    # 1. TON АДРЕС
    if query.startswith(("UQ", "EQ", "0:")) or query.endswith(".ton"):
        try:
            report_data, error = await analyze_ton_wallet_max(query)
            if error:
                return JSONResponse({"error": error})
            return JSONResponse({
                "type": "TON Кошелек",
                "result": {
                    "Баланс": report_data['balance'] + " TON",
                    "В USD": report_data['balance_usd'],
                    "Всего получено": report_data['total_in'] + " TON",
                    "Всего отправлено": report_data['total_out'] + " TON",
                    "Тип кошелька": report_data['profile']['type'],
                    "Контрагентов": report_data['peers_count']
                }
            })
        except Exception as e:
            return JSONResponse({"error": f"Ошибка TON: {str(e)}"})

    # 2. ТЕЛЕФОН
    elif query.startswith("+") and query[1:].isdigit():
        try:
            operator, region = get_phone_info(query)
            clean = query.replace("+", "").replace(" ", "")
            user_info = "Не привязан"
            try:
                user = await user_app.get_users(query)
                if user:
                    user_info = f"{user.first_name or ''} @{user.username}" if user.username else user.first_name
            except: pass
            
            return JSONResponse({
                "type": "Телефон",
                "result": {
                    "Номер": clean,
                    "Оператор": operator,
                    "Регион": region,
                    "Telegram": user_info
                }
            })
        except Exception as e:
            return JSONResponse({"error": f"Ошибка телефона: {str(e)}"})

    # 3. ПОЛЬЗОВАТЕЛЬ ИЛИ ССЫЛКА (Telegram)
    elif query.startswith("@") or "t.me/" in query or query.isdigit():
        username = query.replace("https://", "").replace("http://", "").replace("t.me/", "")
        try:
            user = await user_app.get_users(username)
            if not user:
                return JSONResponse({"error": "Пользователь не найден"})
                
            return JSONResponse({
                "type": "Telegram Профиль",
                "result": {
                    "Имя": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто",
                    "Username": f"@{user.username}" if user.username else "Нет",
                    "ID": user.id,
                    "DC": user.dc_id or "Неизвестно"
                }
            })
        except Exception as e:
            return JSONResponse({"error": "Не удалось получить данные пользователя"})

    return JSONResponse({"error": "Неизвестный формат запроса"})

def run_fastapi():
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (ИЗ ВАШЕГО СТАРОГО КОДА)
# ==========================================
def get_phone_info(phone: str):
    try:
        parsed = phonenumbers.parse(phone, "RU")
        operator = carrier.name_for_number(parsed, "ru") or "Неизвестный оператор"
        region = geocoder.description_for_number(parsed, "ru") or "Неизвестный регион"
        return operator, region
    except:
        return "Неизвестный оператор", "Неизвестный регион"

def to_friendly(addr):
    return addr # Упрощено для Mini App

async def analyze_ton_wallet_max(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200: return None, "Кошелек не найден."
                data = await r.json()
                balance = float(data.get("balance", 0)) / 10**9
            
            # Упрощенный сбор статистики для Mini App
            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=100", headers=headers) as r:
                txs = (await r.json()).get("transactions", [])
            
            return {
                "balance": f"{balance:.2f}",
                "balance_usd": f"${balance * 5.2:.2f}", # Примерный курс
                "total_in": "Загружается...",
                "total_out": "Загружается...",
                "profile": {"type": "Кошелек"},
                "peers_count": len(txs)
            }, None
    except Exception as e:
        return None, str(e)

# ==========================================
# ОБРАБОТЧИКИ БОТА
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    # URL для Mini App берем из окружения Render или используем localhost
    web_app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://ваш-сайт.onrender.com")
    
    await message.reply(
        "👋 Привет! Отправь мне:\n"
        "1. <b>@username</b> или <b>+номер</b>\n"
        "2. <b>TON-адрес</b>\n"
        "3. <b>Стикер</b>\n\n"
        "👇 Или открой удобный поиск:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Открыть OSINT Поиск", web_app=WebAppInfo(url=web_app_url))]
        ])
    )

@bot.on_message(filters.sticker & filters.private)
async def handle_sticker(client, message):
    # (Здесь ваш старый код обработки стикера без ID создателя)
    sticker = message.sticker
    if not sticker.set_name:
        return await message.reply("Стикер не привязан к паку.")
    await message.reply(f"🎨 Пак: {sticker.set_name}\nЭмодзи: {sticker.emoji}")

# ==========================================
# ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("🚀 Запуск веб-сервера FastAPI...")
    threading.Thread(target=run_fastapi, daemon=True).start()
    
    print("🤖 Запуск Telegram-бота...")
    user_app.start()
    bot.run()
