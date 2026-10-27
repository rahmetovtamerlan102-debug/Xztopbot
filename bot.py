import os
import threading
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from pyrogram import Client, filters, raw
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.errors import UsernameNotOccupied, PeerIdInvalid, FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import phonenumbers
from phonenumbers import geocoder, carrier

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)

# ==========================================
# FASTAPI
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

async def get_profile_gifts(user_id: int):
    """Получает подарки, прикрепленные к профилю."""
    try:
        peer = await user_app.resolve_peer(user_id)
        # exclude_unsaved=True оставляет только те, что в профиле
        result = await user_app.invoke(
            GetSavedStarGifts(
                peer=peer,
                offset="",
                limit=20,
                exclude_unsaved=True
            )
        )
        
        if not result or not result.gifts:
            return "Нет"

        gift_list = []
        total_stars = 0
        for g in result.gifts:
            stars = getattr(g.gift, 'stars', 0)
            if stars == 0:
                continue
            
            sender = "Аноним"
            if getattr(g, 'from_id', None):
                if hasattr(g.from_id, 'user_id'):
                    sender = str(g.from_id.user_id)
                else:
                    sender = str(g.from_id)
            
            gift_list.append(f"{stars}⭐ от {sender}")
            total_stars += stars

        if not gift_list:
            return "Нет"

        summary = f"{len(gift_list)} шт. (Сумма: {total_stars}⭐)"
        details = "\n".join(gift_list[:5])
        return f"{summary}\n{details}"

    except FloodWait:
        return "FloodWait (много запросов)"
    except Exception as e:
        print(f"[GIFT ERROR] {e}")
        return f"Ошибка: {type(e).__name__}"

@app.post("/api/search")
async def handle_search(req: SearchRequest):
    query = req.query.strip()
    
    # 1. TON
    if query.startswith(("UQ", "EQ", "0:")) or query.endswith(".ton"):
        return JSONResponse({"type": "TON", "result": {"Статус": "Функция в разработке"}})

    # 2. ТЕЛЕФОН
    elif query.startswith("+") and query[1:].isdigit():
        try:
            parsed = phonenumbers.parse(query, "RU")
            operator = carrier.name_for_number(parsed, "ru") or "Неизвестно"
            region = geocoder.description_for_number(parsed, "ru") or "Неизвестно"
            
            result_data = {
                "Номер": query.replace("+", ""),
                "Оператор": operator,
                "Регион": region,
                "Telegram": "Не привязан"
            }

            try:
                user = await user_app.get_users(query)
                if user:
                    result_data["Telegram"] = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
                    result_data["Username"] = f"@{user.username}" if user.username else "Нет"
                    result_data["ID"] = str(user.id)
                    result_data["DC"] = str(user.dc_id or "Неизвестно")
                    result_data["Подарки"] = await get_profile_gifts(user.id)
            except (UsernameNotOccupied, PeerIdInvalid):
                pass
            except Exception as e:
                result_data["Telegram"] = f"Ошибка: {type(e).__name__}"

            return JSONResponse({"type": "Телефон", "result": result_data})
        except Exception as e:
            return JSONResponse({"error": f"Ошибка телефона: {str(e)}"})

    # 3. USERNAME / ID / ССЫЛКА
    elif query.startswith("@") or "t.me/" in query or query.isdigit():
        username = query.replace("https://", "").replace("http://", "").replace("t.me/", "")
        if username.startswith("@"): username = username[1:]
        
        try:
            user = await user_app.get_users(int(username) if username.isdigit() else username)
            if not user:
                return JSONResponse({"error": "Пользователь не найден"})

            result_data = {
                "Имя": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто",
                "Username": f"@{user.username}" if user.username else "Нет",
                "ID": str(user.id),
                "DC": str(user.dc_id or "Неизвестно"),
                "Подарки": await get_profile_gifts(user.id)
            }
            return JSONResponse({"type": "Telegram Профиль", "result": result_data})
        except Exception as e:
            return JSONResponse({"error": "Не удалось получить данные"})

    return JSONResponse({"error": "Неизвестный формат"})

def run_fastapi():
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

# ==========================================
# БОТ
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    web_app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://ваш-сайт.onrender.com")
    await message.reply(
        "👋 Привет! Открой поиск:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Открыть OSINT Поиск", web_app=WebAppInfo(url=web_app_url))]
        ])
    )

if __name__ == "__main__":
    print("🚀 Запуск FastAPI...")
    threading.Thread(target=run_fastapi, daemon=True).start()
    print("🤖 Запуск бота...")
    user_app.start()
    bot.run()
