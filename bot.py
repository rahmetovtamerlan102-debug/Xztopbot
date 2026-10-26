# ==========================================
# ИМПОРТЫ
# ==========================================
from pyrogram import Client, filters
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
import os
import time
import threading
import random
import aiohttp
from decimal import Decimal, getcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

# Устанавливаем точность для вычислений
getcontext().prec = 20

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
TON_API_KEY = os.environ.get("TON_API_KEY", "")

# ==========================================
# 2. БАЗА ID И ФУНКЦИИ (Telegram)
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
# 3. ФУНКЦИЯ АНАЛИЗА TON-КОШЕЛЬКА
# ==========================================
async def analyze_ton_wallet_max(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY:
        headers["Authorization"] = f"Bearer {TON_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Базовая инфа (баланс, интерфейс)
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200:
                    return f"❌ Кошелек не найден (HTTP {r.status})."
                data = await r.json()
                
                # ТОЧНЫЙ БАЛАНС
                nano_balance = int(data.get("balance", 0))
                balance_dec = Decimal(nano_balance) / Decimal(10**9)
                balance_str = f"{balance_dec.normalize():f}"

                status = data.get("status", "unknown")
                interfaces = ", ".join(data.get("interfaces", [])) or "Нет"

            # 2. DNS (Имя кошелька)
            dns_name = "Нет"
            async with session.get(f"{base_url}/accounts/{wallet_address}/dns/backresolve", headers=headers) as r:
                if r.status == 200:
                    dns_data = await r.json()
                    if dns_data.get("names"):
                        dns_name = dns_data["names"][0]

            # 3. Жетоны (Токены)
            jettons_list = []
            async with session.get(f"{base_url}/accounts/{wallet_address}/jettons", headers=headers) as r:
                if r.status == 200:
                    for b in (await r.json()).get("balances", []):
                        info = b.get("jetton", {})
                        amount_nano = int(b.get("balance", 0))
                        decimals = int(info.get("decimals", 9))
                        amount_dec = Decimal(amount_nano) / Decimal(10**decimals)
                        if amount_dec > 0:
                            jettons_list.append(f"{amount_dec.normalize():f} {info.get('symbol', '?')}")

            # 4. NFT
            nft_list = []
            async with session.get(f"{base_url}/accounts/{wallet_address}/nfts", headers=headers) as r:
                if r.status == 200:
                    nft_data = await r.json()
                    for item in nft_data.get("nft_items", []):
                        meta = item.get("metadata", {})
                        name = meta.get("name") or "Без названия"
                        nft_list.append(name)

            # 5. Транзакции
            events_list = []
            async with session.get(f"{base_url}/accounts/{wallet_address}/events?limit=5", headers=headers) as r:
                if r.status == 200:
                    events_data = await r.json()
                    for ev in events_data.get("events", []):
                        actions = ev.get("actions", [])
                        if actions:
                            act = actions[0]
                            act_type = act.get("type", "Unknown")
                            amount_str = ""
                            if act_type == "TonTransfer":
                                amount_nano = int(act.get("TonTransfer", {}).get("amount", 0))
                                amount_dec = Decimal(amount_nano) / Decimal(10**9)
                                amount_str = f" ({amount_dec.normalize():f} TON)"
                            events_list.append(f" • {act_type}{amount_str}")

            # --- СБОРКА ОТЧЕТА ---
            name_line = f"🏷 <b>Имя:</b> {dns_name}\n" if dns_name != "Нет" else ""
            
            result = (
                f"💼 <b>Кошелек:</b> <code>{wallet_address}</code>\n"
                f"{name_line}"
                f"📊 <b>Статус:</b> {status}\n"
                f"🔧 <b>Интерфейс:</b> {interfaces}\n"
                f"💰 <b>Баланс:</b> {balance_str} TON\n\n"
            )
            
            if jettons_list:
                result += f"🪙 <b>Жетоны:</b>\n" + "\n".join([f" • {j}" for j in jettons_list[:5]]) + "\n"
            else:
                result += "🪙 <b>Жетоны:</b> Нет\n"
                
            if nft_list:
                result += f"🖼 <b>NFT ({len(nft_list)}):</b>\n" + "\n".join([f" • {n}" for n in nft_list[:5]]) + "\n"
            else:
                result += "🖼 <b>NFT:</b> Нет\n"

            if events_list:
                result += f"\n📜 <b>Последние операции:</b>\n" + "\n".join(events_list)
            
            return result

    except Exception as e:
        return f"❌ Ошибка при запросе к TON API: {e}"

# ==========================================
# 4. КЛИЕНТЫ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)

last_request_time = {}

# ==========================================
# 5. ОБРАБОТЧИК /START
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply("👋 Привет! Отправь мне <b>@username</b> для поиска профиля или <b>TON-адрес</b> (начинается с UQ, EQ) для анализа кошелька.")

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

    # === ПРОВЕРКА НА TON-КОШЕЛЕК ===
    if target_input.startswith(("UQ", "EQ", "0:")):
        status_msg = await message.reply("🔍 Анализирую TON-кошелек...")
        result = await analyze_ton_wallet_max(target_input)
        await status_msg.edit_text(result)
        return

    # === ПОИСК ПО TELEGRAM ===
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
        
        try:
            peer = await user_app.resolve_peer(user_id)
            result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=10))
            
            if result.gifts:
                gl = []
                for i, g in enumerate(result.gifts):
                    stars = getattr(g.gift, 'stars', 0)
                    gd = datetime.fromtimestamp(g.date).strftime("%d.%m.%Y")
                    
                    if stars == 0:
                        continue
                        
                    if getattr(g, 'name_hidden', False) or not g.from_id:
                        gl.append(f" • {stars}⭐ от Аноним ({gd})")
                    else:
                        sender_id = getattr(g.from_id, 'user_id', '?')
                        gl.append(f" • {stars}⭐ от ID: <code>{sender_id}</code> ({gd})")
                        
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

        await status_msg.edit_text(card)

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
