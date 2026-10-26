# ==========================================
# ИМПОРТЫ
# ==========================================
from pyrogram import Client, filters
import os
import time
import threading
import aiohttp
import io
from decimal import Decimal, getcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from xhtml2pdf import pisa

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
TON_API_KEY = os.environ.get("TON_API_KEY", "")

# ==========================================
# 2. АНАЛИЗ TON-КОШЕЛЬКА (Сбор данных)
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
                        utime = tx.get("now", 0)
                        timestamps.append(utime)
                        date_str = datetime.fromtimestamp(utime).strftime("%d.%m.%Y %H:%M")
                        
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
            
            first_date = datetime.fromtimestamp(timestamps[-1]).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            last_date = datetime.fromtimestamp(timestamps[0]).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"

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
# 3. ГЕНЕРАЦИЯ HTML -> PDF (КРАСИВЫЙ ДИЗАЙН)
# ==========================================
def generate_aml_pdf(data: dict):
    # Генерируем строки таблицы транзакций
    tx_rows = ""
    for tx in data['txs']:
        color = "#28a745" if tx['type'] == "IN" else "#dc3545" # Зеленый для IN, красный для OUT
        sign = "+" if tx['type'] == "IN" else "-"
        tx_rows += f"""
        <tr>
            <td style="color: {color}; font-weight: bold;">[{tx['type']}]</td>
            <td>{sign}{tx['amount']} TON</td>
            <td>{tx['peer']}</td>
            <td>{tx['date']}</td>
        </tr>
        """

    # HTML шаблон с CSS стилями
    html = f"""
    <html>
    <head>
        <style>
            @page {{ size: A4; margin: 1cm; }}
            body {{ font-family: Helvetica, sans-serif; color: #333; font-size: 10pt; }}
            .header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; margin-bottom: 20px; }}
            .header h1 {{ margin: 0; color: #2c3e50; }}
            .section {{ margin-bottom: 20px; }}
            .section-title {{ background-color: #f8f9fa; padding: 8px; font-weight: bold; border-left: 4px solid #2c3e50; margin-bottom: 10px; font-size: 12pt; }}
            .info-table {{ width: 100%; border-collapse: collapse; }}
            .info-table td {{ padding: 5px 0; border-bottom: 1px solid #eee; }}
            .info-table td.label {{ font-weight: bold; width: 40%; color: #555; }}
            .badge-risk {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: bold; color: white; background-color: #ffc107; }}
            .badge-safe {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-weight: bold; color: white; background-color: #28a745; }}
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
                        <th>Тип</th>
                        <th>Сумма</th>
                        <th>Контрагент</th>
                        <th>Дата и время</th>
                    </tr>
                </thead>
                <tbody>
                    {tx_rows}
                </tbody>
            </table>
        </div>

        <div class="footer">
            Отчет сформирован автоматически по данным публичного блокчейна TON.
        </div>
    </body>
    </html>
    """

    # Конвертация HTML в PDF
    pdf_buffer = io.BytesIO()
    pisa.CreatePDF(html, dest=pdf_buffer)
    pdf_buffer.seek(0)
    return pdf_buffer

# ==========================================
# 4. КЛИЕНТ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
last_request_time = {}

# ==========================================
# 5. ОБРАБОТЧИК
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply("Отправь мне TON-адрес (UQ...), и я сделаю AML-анализ и пришлю PDF-отчет.")

@bot.on_message(filters.text & filters.private & ~filters.command("help"))
async def handle_message(client, message):
    user_id_requester = message.from_user.id
    current_time = time.time()
    if user_id_requester in last_request_time:
        if current_time - last_request_time[user_id_requester] < 10:
            await message.reply("Подождите 10 секунд.")
            return
    last_request_time[user_id_requester] = current_time

    target_input = message.text.strip()

    if target_input.startswith(("UQ", "EQ", "0:")):
        status_msg = await message.reply("Собираю данные и генерирую PDF-отчет...")
        
        report_data, error = await analyze_ton_wallet_max(target_input)
        
        if error:
            await status_msg.edit_text(error)
            return
            
        pdf_buffer = generate_aml_pdf(report_data)
        
        await message.reply_document(
            document=pdf_buffer,
            file_name=f"AML_Report_{target_input[:6]}.pdf",
            caption=f"AML-Анализ кошелька\n`{target_input}`\n\nТранзитность: {report_data['is_transit']}"
        )
        await status_msg.delete()
        return

    await message.reply("Пожалуйста, отправьте корректный TON-адрес (начинается с UQ или EQ).")

# ==========================================
# 6. ЗАПУСК
# ==========================================
if __name__ == "__main__":
    print("Бот запущен...")
    bot.run()
