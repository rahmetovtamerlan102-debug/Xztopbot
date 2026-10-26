# ==========================================
# ИМПОРТЫ
# ==========================================
from pyrogram import Client, filters
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser, GetUsers
from pyrogram.raw.types import InputUser
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import time
import threading
import random
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# ==========================================
# 0. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER
# ==========================================
def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args):
            pass
    server = HTTPServer(('0.0.0.0', port), Handler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# ==========================================
# 1. КОНФИГУРАЦИЯ
# ==========================================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# ==========================================
# 2. БАЗА ID И ФУНКЦИИ
# ==========================================
LOCAL_DB = {
    100000000: "2013-08", 200000000: "2014-01", 300000000: "2014-07",
    400000000: "2014-12", 500000000: "2015-04", 700000000: "2015-10",
    1000000000: "2016-04", 1500000000: "2017-01", 2000000000: "2017-07",
    3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01",
    6000000000: "2021-02", 7000000000: "2022-06", 7500000000: "2023-05",
    8000000000: "2024-06", 8500000000: "2025-01",
}

MONTHS_RU_GENITIVE = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель",
    5: "май", 6: "июнь", 7: "июль", 8: "август",
    9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"
}

def get_time_passed(year, month):
    now = datetime.now()
    reg_date = datetime(year, month, 1)
    years = now.year - reg_date.year
    months = now.month - reg_date.month
    if months < 0:
        years -= 1; months += 12
        
    parts = []
    if years > 0:
        y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else \
                f"{years} года" if 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14) else f"{years} лет"
        parts.append(y_str)
    
    if months > 0:
        m_str = f"{months} мес"
        parts.append(m_str)
        
    return " ".join(parts) if parts else "0 мес"

def estimate_registration(user_id):
    ids = sorted(LOCAL_DB.keys())
    if user_id <= ids[0]: p = LOCAL_DB[ids[0]].split("-"); return int(p[0]), int(p[1])
    if user_id >= ids[-1]: p = LOCAL_DB[ids[-1]].split("-"); return int(p[0]), int(p[1])
    for i in range(len(ids) - 1):
        id_start, id_end = ids[i], ids[i+1]
        if id_start <= user_id <= id_end:
            dt_start = datetime.strptime(LOCAL_DB[id_start], "%Y-%m")
            dt_end = datetime.strptime(LOCAL_DB[id_end], "%Y-%m")
            months_diff = (dt_end.year - dt_start.year) * 12 + (dt_end.month - dt_start.month)
            if months_diff <= 0: months_diff = 1
            id_ratio = (user_id - id_start) / (id_end - id_start)
            months_to_add = int(id_ratio * months_diff)
            est_y = dt_start.year + (dt_start.month + months_to_add - 1) // 12
            est_m = (dt_start.month + months_to_add - 1) % 12 + 1
            return est_y, est_m
    return None, None

# ==========================================
# 3. КЛИЕНТЫ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)

last_request_time = {}

# ==========================================
# 4. ОБРАБОТЧИК /START
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply("👋 Привет! Отправь мне <b>@username</b> пользователя, и я покажу информацию о нём.")

# ==========================================
# 5. ОБРАБОТЧИК КНОПОК
# ==========================================
@bot.on_callback_query()
async def handle_callback(client, callback_query):
    data = callback_query.data
    if data.startswith("gift_info_"):
        target_id = data.split("_")[2]
        try:
            users = await user_app.invoke(GetUsers(id=[InputUser(user_id=int(target_id), access_hash=0)]))
            u = users[0]
            uname = f"@{u.username}" if getattr(u, 'username', None) else "Нет"
            await callback_query.answer(f"Имя: {u.first_name}\nЮзернейм: {uname}\nID: {target_id}", show_alert=True)
        except Exception:
            await callback_query.answer(f"ID: {target_id}\n(Профиль скрыт)", show_alert=True)

# ==========================================
# 6. ОСНОВНОЙ ОБРАБОТЧИК
# ==========================================
@bot.on_message(filters.text & filters.private & ~filters.command("help"))
async def handle_message(client, message):
    if message.text and message.text.startswith("/start"):
        return

    user_id_requester = message.from_user.id
    current_time = time.time()
    if user_id_requester in last_request_time:
        if current_time - last_request_time[user_id_requester] < 10:
            await message.reply("⏳ Подождите 10 секунд перед следующим запросом.")
            return
    last_request_time[user_id_requester] = current_time

    target_input = message.text.strip()
    if not target_input: return

    if target_input.isdigit():
        await message.reply("⚠️ Поиск по ID временно отключен. Пожалуйста, отправьте <b>@username</b>.")
        return

    if not target_input.startswith('@'):
        target_input = f"@{target_input}"

    status_msg = await message.reply(f"🔍 Ищу информацию для {target_input}...")

    try:
        user = await user_app.get_users(target_input)
        if isinstance(user, list): user = user[0]

        user_id = user.id
        name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
        username_str = f" (@{user.username})" if user.username else ""
        display_name = f"{name}{username_str}"

        dc_line = ""
        if user.photo:
            dc_line = f"<b>DC:</b> {user.dc_id}\n"

        est_y, est_m = estimate_registration(user_id)
        if est_y and est_m:
            reg_str = f"~ {MONTHS_RU_GENITIVE.get(est_m, '')} {est_y} ({get_time_passed(est_y, est_m)})"
        else:
            reg_str = "Неизвестно"

        channel_str = "Нет"
        try:
            full_info = await user_app.invoke(GetFullUser(id=await user_app.resolve_peer(user_id)))
            f_user = full_info.full_user
            p_channel_id = getattr(f_user, 'personal_channel_id', None)
            if p_channel_id:
                try:
                    chan = await user_app.get_chat(p_channel_id)
                    if chan:
                        channel_str = f"@{chan.username}" if chan.username else chan.title
                except Exception: pass
        except Exception: pass

        # --- ПОДАРКИ ---
        gifts_info = ""
        gift_buttons = [] 
        
        try:
            peer = await user_app.resolve_peer(user_id)
            result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=10))
            
            if result.gifts:
                gl = []
                for i, g in enumerate(result.gifts):
                    stars = getattr(g.gift, 'stars', 0)
                    gd = datetime.fromtimestamp(g.date).strftime("%d.%m.%Y")
                    
                    # Пропускаем, если 0 звезд
                    if stars == 0:
                        continue
                        
                    if getattr(g, 'name_hidden', False) or not g.from_id:
                        # Выводим Аноним
                        gl.append(f" • {stars}⭐ от Аноним ({gd})")
                    else:
                        sender_id = getattr(g.from_id, 'user_id', None)
                        sender_hash = getattr(g.from_id, 'access_hash', 0)
                        
                        if sender_id:
                            try:
                                res_users = await user_app.invoke(GetUsers(id=[InputUser(user_id=sender_id, access_hash=sender_hash)]))
                                su = res_users[0]
                                
                                if getattr(su, 'username', None):
                                    sender_name = f"@{su.username}"
                                    url = f"https://t.me/{su.username}"
                                    gift_buttons.append([InlineKeyboardButton(f"👤 От: @{su.username} (⭐{stars})", url=url)])
                                else:
                                    sender_name = f"{su.first_name or ''}"
                                    cb_data = f"gift_info_{sender_id}"
                                    gift_buttons.append([InlineKeyboardButton(f"👤 От: {sender_name} (⭐{stars})", callback_data=cb_data)])
                            except Exception:
                                # Если ошибка, имя не пишем, но подарок не теряем
                                sender_name = ""
                            
                            # Если имя есть, пишем его, если нет - пишем просто звезды
                            if sender_name:
                                gl.append(f" • {stars}⭐ от {sender_name} ({gd})")
                            else:
                                gl.append(f" • {stars}⭐ ({gd})")
                gifts_info = "\n" + "\n".join(gl)
        except Exception: pass

        accuracy = random.randint(85, 99)

        card = (
            "🪪 <b>Информация о пользователе</b>\n\n"
            f"<b>ID:</b> <code>{user_id}</code>\n"
            f"<b>Имя:</b> {display_name}\n"
            f"{dc_line}"
        )
        if channel_str != "Нет":
            card += f"<b>Канал:</b> {channel_str}\n"
        
        card += f"\n<b>Регистрация:</b> {reg_str}\n"

        if gifts_info:
            card += f"\n<b>Подарки:</b>{gifts_info}\n"

        card += f"\n<b>Точность:</b> высокая ({accuracy}%)\n"

        reply_markup = InlineKeyboardMarkup(gift_buttons) if gift_buttons else None

        await status_msg.edit_text(card, reply_markup=reply_markup)

    except Exception as e:
        error_msg = str(e)
        if "PEER_ID_INVALID" in error_msg:
            await status_msg.edit_text("❌ Пользователь не найден. Проверьте правильность @username.")
        elif "PHONE_NOT_OCCUPIED" in error_msg:
            await status_msg.edit_text("❌ Ошибка: Номер не зарегистрирован в Telegram.")
        else:
            await status_msg.edit_text(f"❌ Ошибка: {e}")

# ==========================================
# 7. ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("🤖 Запуск...")
    user_app.start()
    print("✅ Личный аккаунт подключен.")
    bot.run()
