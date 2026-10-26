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
from fpdf import FPDF
import io

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
# 3. ФУНКЦИЯ АНАЛИЗА TON-КОШЕЛЬКА (Сбор данных)
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
                    return None, f"❌ Кошелек не найден (HTTP {r.status})."
                data = await r.json()
                nano_balance = int(data.get("balance", 0))
                balance_dec = Decimal(nano_balance) / Decimal(10**9)
                balance_str = f"{balance_dec.normalize():f}"
                status = data.get("status", "unknown")
                interfaces = ", ".join(data.get("interfaces", [])) or "Нет"

            # 2. DNS
            dns_name = "Нет"
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
                        utime = tx.get("now", 0)
                        timestamps.append(utime)
                        date_str = datetime.fromtimestamp(utime).strftime("%d.%m.%Y %H:%M")
                        
                        # Входящие
                        in_msg = tx.get("in_msg")
                        if in_msg and in_msg.get("value"):
                            amount_nano = int(in_msg.get("value", 0))
                            amount_dec = Decimal(amount_nano) / Decimal(10**9)
                            
                            if amount_dec > 0:
                                total_in_ton += amount_dec
                                total_txs += 1
                                if amount_dec % 1 == 0: round_sums += 1
                                
                                sender = in_msg.get("source", {}).get("address", "?")
                                if sender and sender != "?": unique_peers.add(sender)
                                sender_short = sender[:6] + "..." + sender[-4:] if len(sender) > 10 else sender
                                tx_list.append(f"📥 {amount_dec.normalize():f} TON от {sender_short} ({date_str})")

                        # Исходящие
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amount_nano = int(out.get("value", 0))
                                amount_dec = Decimal(amount_nano) / Decimal(10**9)
                                
                                if amount_dec > 0:
                                    total_out_ton += amount_dec
                                    total_txs += 1
                                    if amount_dec % 1 == 0: round_sums += 1
                                    
                                    dest = out.get("destination", {}).get("address", "?")
                                    if dest and dest != "?": unique_peers.add(dest)
                                    dest_short = dest[:6] + "..." + dest[-4:] if len(dest) > 10 else dest
                                    tx_list.append(f"📤 {amount_dec.normalize():f} TON кому {dest_short} ({date_str})")

            # --- РАСЧЕТ МЕТРИК ---
            out_percent = (total_out_ton / total_in_ton * 100) if total_in_ton > 0 else 0
            is_transit = "Да (Транзитка)" if out_percent > 90 else "Нет"
            round_percent = (round_sums / total_txs * 100) if total_txs > 0 else 0
            
            first_date_str = datetime.fromtimestamp(timestamps[-1]).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            last_date_str = datetime.fromtimestamp(timestamps[0]).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"

            report_data = {
                "wallet": wallet_address,
                "dns": dns_name,
                "status": status,
                "interfaces": interfaces,
                "balance": balance_str,
                "total_in": f"{total_in_ton.normalize():f}",
                "total_out": f"{total_out_ton.normalize():f}",
                "peers": len(unique_peers),
                "out_percent": f"{out_percent:.1f}",
                "is_transit": is_transit,
                "round_percent": f"{round_percent:.1f}",
                "first_tx": first_date_str,
                "last_tx": last_date_str,
                "txs": tx_list[:15]
            }
            return report_data, None

    except Exception as e:
        return None, f"❌ Ошибка API: {e}"

# ==========================================
# 4. ФУНКЦИЯ ГЕНЕРАЦИИ PDF
# ==========================================
def generate_aml_pdf(data: dict):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "AML-Отчет по TON-кошельку", ln=True, align="C")
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, "1. Информация об адресе", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Адрес: {data['wallet']}", ln=True)
    pdf.cell(0, 6, f"Метка (DNS): {data['dns']}", ln=True)
    pdf.cell(0, 6, f"Статус: {data['status']}", ln=True)
    pdf.cell(0, 6, f"Интерфейс: {data['interfaces']}", ln=True)
    pdf.cell(0, 6, f"Баланс: {data['balance']} TON", ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, "2. AML-Анализ (Оборот)", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Период: с {data['first_tx']} по {data['last_tx']}", ln=True)
    pdf.cell(0, 6, f"Всего входящих: {data['total_in']} TON", ln=True)
    pdf.cell(0, 6, f"Всего исходящих: {data['total_out']} TON", ln=True)
    pdf.cell(0, 6, f"Уникальных контрагентов: {data['peers']}", ln=True)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, "3. Индикаторы риска", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, f"Транзитный адрес (выведено >90%): {data['is_transit']} ({data['out_percent']}%)", ln=True)
    pdf.cell(0, 6, f"Использование круглых сумм: {data['round_percent']}%", ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 10, "4. История транзакций (Последние)", ln=True)
    pdf.set_font("Courier", "", 8)
    for tx in data['txs']:
        # Убираем эмодзи для PDF, так как FPDF их не поддерживает без спец шрифтов
        clean_tx = tx.replace("📥", "[IN]").replace("📤", "[OUT]")
        pdf.cell(0, 4, clean_tx, ln=True)

    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 4, "Отчет сформирован автоматически по данным публичного блокчейна TON.", ln=True)

    # Сохраняем в байты (чтобы не создавать файл на диске, а сразу отправить)
    pdf_bytes = pdf.output(dest="S").encode("latin-1")
    return io.BytesIO(pdf_bytes)

# ==========================================
# 5. КЛИЕНТЫ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)

last_request_time = {}

# ==========================================
# 6. ОСНОВНОЙ ОБРАБОТЧИК
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply("👋 Привет! Отправь мне TON-адрес (UQ...), и я сделаю AML-анализ и пришлю PDF-отчет.")

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
            
        # Генерируем PDF
        pdf_buffer = generate_aml_pdf(report_data)
        
        # Отправляем файл
        await message.reply_document(
            document=pdf_buffer,
            file_name=f"AML_Report_{target_input[:6]}.pdf",
            caption=f"🛡 **AML-Анализ кошелька**\n`{target_input}`\n\nТранзитность: {report_data['is_transit']}",
            parse_mode="markdown"
        )
        await status_msg.delete() # Удаляем сообщение "Ищу..."
        return

    await message.reply("⚠️ Пожалуйста, отправьте корректный TON-адрес (начинается с UQ или EQ).")

# ==========================================
# 7. ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("🤖 Запуск...")
    user_app.start()
    print("✅ Личный аккаунт подключен.")
    bot.run()
