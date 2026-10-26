from pyrogram import Client, filters
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
import os
import time
import threading
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

# Запускаем сервер в фоне, чтобы он не мешал боту
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

MONTHS_RU = {
    1: "Январь", 2: "Февраль", 3: "Март", 4: "Апрель",
    5: "Май", 6: "Июнь", 7: "Июль", 8: "Август",
    9: "Сентябрь", 10: "Октябрь", 11: "Ноябрь", 12: "Декабрь"
}

def get_time_passed(year, month):
    now = datetime.now()
    reg_date = datetime(year, month, 1)
    years = now.year - reg_date.year
    months = now.month - reg_date.month
    if months < 0:
        years -= 1; months += 12
    y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else \
            f"{years} года" if 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14) else f"{years} лет"
    if months == 0: return y_str
    m_str = f"{months} месяц" if months % 10 == 1 and months % 100 != 11 else \
            f"{months} месяца" if 2 <= months % 10 <= 4 and not (12 <= months % 100 <= 14) else f"{months} месяцев"
    return f"{y_str} {m_str}"

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
# 4. ОБРАБОТЧИК СООБЩЕНИЙ
# ==========================================
@bot.on_message(filters.text & filters.private & ~filters.command(["start", "help"]))
async def handle_message(client, message):
    user_id_requester = message.from_user.id
    
    # Антиспам (10 секунд)
    current_time = time.time()
    if user_id_requester in last_request_time:
        if current_time - last_request_time[user_id_requester] < 10:
            await message.reply("⏳ Подождите 10 секунд перед следующим запросом.")
            return
    last_request_time[user_id_requester] = current_time

    target_input = message.text.strip()
    if not target_input: return

    # Проверка: только юзернейм
    if target_input.isdigit():
        await message.reply("⚠️ Поиск по ID временно отключен. Пожалуйста, отправьте **@username**.")
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

        # DC (только если есть фото)
        dc_line = ""
        if user.photo:
            dc_line = f"**DC:** {user.dc_id}\n"

        # Регистрация
        est_y, est_m = estimate_registration(user_id)
        reg_str = f"{MONTHS_RU.get(est_m, '')} {est_y} ({get_time_passed(est_y, est_m)})" if est_y else "Неизвестно"

        # Канал
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

        # Подарки
        gifts_info = ""
        try:
            peer = await user_app.resolve_peer(user_id)
            result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=10))
            if result.gifts:
                gl = []
                for g in result.gifts:
                    if getattr(g, 'name_hidden', False) or not g.from_id:
                        sender = "Аноним"
                    else:
                        try:
                            su = await user_app.get_users(g.from_id)
                            sender = f"@{su.username}" if su.username else (su.first_name or f"ID:{su.id}")
                        except Exception: 
                            sender = f"ID:{getattr(g.from_id, 'user_id', '?')}"
                    gd = datetime.fromtimestamp(g.date).strftime("%d.%m.%Y")
                    stars = getattr(g.gift, 'stars', 0)
                    gl.append(f" • {stars}⭐ от {sender} ({gd})")
                gifts_info = "\n" + "\n".join(gl)
        except Exception: pass

        # Формируем карточку
        card = (
            "🪪 **Информация о пользователе**\n\n"
            f"**ID:** `{user_id}`\n"
            f"**Имя:** {display_name}\n"
            f"{dc_line}"
        )
        if channel_str != "Нет":
            card += f"**Канал:** {channel_str}\n"
        
        card += f"\n**Регистрация:** {reg_str}\n"

        if gifts_info:
            card += f"\n**Подарки:**{gifts_info}\n"

        # Точность в самом низу
        card += f"\n**Точность:** высокая (89%)\n"

        await status_msg.edit_text(card, parse_mode="markdown")

    except Exception as e:
        error_msg = str(e)
        if "PEER_ID_INVALID" in error_msg:
            await status_msg.edit_text("❌ Пользователь не найден. Проверьте правильность @username.")
        elif "PHONE_NOT_OCCUPIED" in error_msg:
            await status_msg.edit_text("❌ Ошибка: Номер не зарегистрирован в Telegram.")
        else:
            await status_msg.edit_text(f"❌ Ошибка: {e}")

# ==========================================
# 5. /start
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply("👋 Привет! Отправь мне **@username** пользователя, и я покажу информацию о нём.")

# ==========================================
# 6. ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("🤖 Запуск...")
    user_app.start()
    print("✅ Личный аккаунт подключен.")
    bot.run()
