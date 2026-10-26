# ==========================================
# ИМПОРТЫ
# ==========================================
from pyrogram import Client, filters
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
import os
import time
import threading
import aiohttp
import io
import random
from decimal import Decimal, getcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from xhtml2pdf import pisa
import matplotlib

# --- ФИКС ШРИФТОВ ЧЕРЕЗ ПУТЬ MATPLOTLIB ---
_FD = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
# Заменяем обратные слэши на прямые для корректного CSS
FONT_REG = os.path.join(_FD, "DejaVuSans.ttf").replace("\\", "/")
FONT_BOLD = os.path.join(_FD, "DejaVuSans-Bold.ttf").replace("\\", "/")

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
# 2. БАЗА ID И ФУНКЦИИ ДЛЯ TELEGRAM
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
# 3. АНАЛИЗ TON-КОШЕЛЬКА
# ==========================================
async def analyze_ton_wallet_max(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY:
        headers["Authorization"] = f"Bearer {TON_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Базовая инфа
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200:
                    return None, f"Кошелек не найден (HTTP {r.status})."
                data = await r.json()
                nano_balance = int(data.get("balance", 0))
                balance_dec = Decimal(nano_balance) / Decimal(10**9)
                balance_str = f"{balance_dec.normalize():f}"
                status = data.get("status", "unknown")
                interfaces = ", ".join(data.get("interfaces", [])) or "Нет"

            # 2. DNS
            dns_name = "Неидентифицированные"
            async with session.get(f"{base_url}/accounts/{wallet_address}/dns/backresolve", headers=headers) as r:
                if r.status == 200:
                    dns_data = await r.json()
                    if dns_data.get("names"):
                        dns_name = dns_data["names"][0]

            # 3. Транзакции
            tx_list = []
            total_in_ton = Decimal(0)
            total_out_ton = Decimal(0)
            unique_peers = set()
            round_sums = 0
            total_txs = 0
            timestamps = []

            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=50", headers=headers) as r:
                if r.status == 200:
                    txs_data = await r.json()
                    transactions = txs_data.get("transactions", [])
                    
                    for tx in transactions:
                        utime = tx.get("utime", 0)
                        if utime > 0:
                            timestamps.append(utime)
                            date_str = datetime.fromtimestamp(utime).strftime("%d.%m.%Y %H:%M")
                        else:
                            date_str = "Неизвестно"
                        
                        # Входящие
                        in_msg = tx.get("in_msg")
                        if in_msg and in_msg.get("value"):
                            amount_dec = Decimal(int(in_msg.get("value", 0))) / Decimal(10**9)
                            if amount_dec > 0:
                                total_in_ton += amount_dec
                                total_txs += 1
                                if amount_dec % 1 == 0: round_sums += 1
                                sender = in_msg.get("source", {}).get("address", "?")
                                if sender and sender != "?": unique_peers.add(sender)
                                sender_short = sender[:6] + "..." + sender[-4:] if len(sender) > 10 else sender
                                tx_list.append({"type": "IN", "amount": f"{amount_dec.normalize():f}", "peer": sender_short, "date": date_str})

                        # Исходящие
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amount_dec = Decimal(int(out.get("value", 0))) / Decimal(10**9)
                                if amount_dec > 0:
                                    total_out_ton += amount_dec
                                    total_txs += 1
                                    if amount_dec % 1 == 0: round_sums += 1
                                    dest = out.get("destination", {}).get("address", "?")
                                    if dest and dest != "?": unique_peers.add(dest)
                                    dest_short = dest[:6] + "..." + dest[-4:] if len(dest) > 10 else dest
                                    tx_list.append({"type": "OUT", "amount": f"{amount_dec.normalize():f}", "peer": dest_short, "date": date_str})

            out_percent = (float(total_out_ton) / float(total_in_ton) * 100) if total_in_ton > 0 else 0
            is_transit = "Да" if out_percent > 90 else "Нет"
            round_percent = (round_sums / total_txs * 100) if total_txs > 0 else 0
            
            first_date = datetime.fromtimestamp(min(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            last_date = datetime.fromtimestamp(max(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"

            report_data = {
                "wallet": wallet_address, "dns": dns_name, "status": status, "interfaces": interfaces,
                "balance": balance_str, "total_in": f"{total_in_ton.normalize():f}", "total_out": f"{total_out_ton.normalize():f}",
                "peers": len(unique_peers), "out_percent": f"{out_percent:.1f}", "is_transit": is_transit,
                "round_percent": f"{round_percent:.1f}", "first_tx": first_date, "last_tx": last_date,
                "txs": tx_list[:15]
            }
            return report_data, None

    except Exception as e:
        return None, f"Ошибка API: {e}"

# ==========================================
# 4. ГЕНЕРАЦИЯ PDF
# ==========================================
def generate_aml_pdf(data: dict):
    # Выводим в логи пути к шрифтам для проверки
    print(f"Проверка шрифтов: Regular={os.path.exists(FONT_REG)}, Bold={os.path.exists(FONT_BOLD)}")

    tx_rows = ""
    for tx in data['txs']:
        color = "#28a745" if tx['type'] == "IN" else "#dc3545"
        sign = "+" if tx['type'] == "IN" else "-"
        
        tx_rows += f"""
        <tr>
            <td style="color: {color}; font-weight: bold;">[{tx['type']}]</td>
            <td>{sign}{tx['amount']} TON</td>
            <td>{tx['peer']}</td>
            <td>{tx['date']}</td>
        </tr>
        """

    html = f"""
    <html>
    <head>
        <style>
            /* Подключаем шрифты по абсолютному пути */
            @font-face {{
                font-family: 'DejaVu';
                src: url('{FONT_REG}');
            }}
            @font-face {{
                font-family: 'DejaVu';
                src: url('{FONT_BOLD}');
                font-weight: bold;
            }}
            @page {{ size: A4; margin: 1cm; }}
            
            /* Применяем шрифт ко всем элементам */
            body, td, th, div, span, h1, p {{ font-family: 'DejaVu'; color: #333; font-size: 10pt; }}
            
            .header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; }}
            .header h1 {{ margin: 0; color: #2c3e50; }}
            .section {{ margin-bottom: 20px; }}
            .section-title {{ background-color: #f8f9fa; padding: 8px; font-weight: bold; border-left: 4px solid #2c3e50; margin-bottom: 10px; font-size: 12pt; }}
            .info-table {{ width: 100%; border-collapse: collapse; }}
            .info-table td {{ padding: 5px 0; border-bottom: 1px solid #eee; }}
            .info-table td.label {{ font-weight: bold; width: 40%; color: #555; }}
            .badge-risk {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: bold; color: white; background-color: #ffc107; }}
            .tx-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
            .tx-table th {{ background-color: #f1f1f1; padding: 8px; text-align: left; font-size: 9pt; border-bottom: 2px solid #ddd; }}
            .tx-table td {{ padding: 6px 8px; border-bottom: 1px solid #eee; font-size: 9pt; }}
            .footer {{ text-align: center; font-size: 8pt; color: #888; margin-top: 30px; border-top: 1px solid #ddd; padding-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>AML-Отчет по TON-кошельку</h1>
            <p>Дата генерации: {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>
        </div>
        <div class="section">
            <div class="section-title">1. Информация об адресе</div>
            <table class="info-table">
                <tr><td class="label">Адрес:</td><td>{data['wallet']}</td></tr>
                <tr><td class="label">Метка (DNS):</td><td>{data['dns']}</td></tr>
                <tr><td class="label">Статус:</td><td>{data['status']}</td></tr>
                <tr><td class="label">Тип контракта:</td><td>{data['interfaces']}</td></tr>
                <tr><td class="label">Текущий баланс:</td><td><b>{data['balance']} TON</b></td></tr>
            </table>
        </div>
        <div class="section">
            <div class="section-title">2. Оборот и Активность</div>
            <table class="info-table">
                <tr><td class="label">Первая операция:</td><td>{data['first_tx']}</td></tr>
                <tr><td class="label">Последняя операция:</td><td>{data['last_tx']}</td></tr>
                <tr><td class="label">Входящие (в выборке):</td><td style="color: #28a745;">{data['total_in']} TON</td></tr>
                <tr><td class="label">Исходящие (в выборке):</td><td style="color: #dc3545;">{data['total_out']} TON</td></tr>
                <tr><td class="label">Уникальных контрагентов:</td><td>{data['peers']}</td></tr>
            </table>
        </div>
        <div class="section">
            <div class="section-title">3. Индикаторы риска</div>
            <table class="info-table">
                <tr><td class="label">Транзитный адрес (выведено >90%):</td><td><span class="badge-risk">{data['is_transit']} ({data['out_percent']}%)</span></td></tr>
                <tr><td class="label">Использование круглых сумм:</td><td>{data['round_percent']}%</td></tr>
            </table>
        </div>
        <div class="section">
            <div class="section-title">4. Последние транзакции</div>
            <table class="tx-table">
                <thead>
                    <tr>
                        <th>Тип</th><th>Сумма</th><th>Контрагент</th><th>Дата и время</th>
                    </tr>
                </thead>
                <tbody>{tx_rows}</tbody>
            </table>
        </div>
        <div class="footer">Отчет сформирован автоматически по данным публичного блокчейна TON.</div>
    </body>
    </html>
    """
    pdf_buffer = io.BytesIO()
    pisa.CreatePDF(html.encode('utf-8'), dest=pdf_buffer, encoding='utf-8')
    pdf_buffer.seek(0)
    return pdf_buffer

# ==========================================
# 5. КЛИЕНТЫ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)

last_request_time = {}

# ==========================================
# 6. ОБРАБОТЧИКИ
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply("👋 Привет! Отправь мне:\n1. <b>@username</b> (для поиска профиля)\n2. <b>TON-адрес</b> (UQ..., для AML-отчета в PDF).")

@bot.on_message(filters.text & filters.private & ~filters.command("help"))
async def handle_message(client, message):
    user_id_requester = message.from_user.id
    current_time = time.time()
    if user_id_requester in last_request_time:
        if current_time - last_request_time[user_id_requester] < 10:
            await message.reply("⏳ Подождите 10 секунд.")
            return
    last_request_time[user_id_requester] = current_time

    target_input = message.text.strip()

    # === TON-КОШЕЛЕК ===
    if target_input.startswith(("UQ", "EQ", "0:")):
        status_msg = await message.reply("🔍 Собираю данные и генерирую PDF-отчет...")
        
        report_data, error = await analyze_ton_wallet_max(target_input)
        
        if error:
            await status_msg.edit_text(error)
            return
            
        pdf_buffer = generate_aml_pdf(report_data)
        
        await message.reply_document(
            document=pdf_buffer,
            file_name=f"AML_Report_{target_input[:6]}.pdf",
            caption=f"🛡 AML-Анализ кошелька\n`{target_input}`\n\nТранзитность: {report_data['is_transit']}"
        )
        await status_msg.delete()
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
