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
import matplotlib.pyplot as plt
import matplotlib
from decimal import Decimal, getcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone, timedelta
from xhtml2pdf import pisa
import base64

# --- ФИКС ШРИФТОВ ---
_FD = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
FONT_REG = os.path.join(_FD, "DejaVuSans.ttf").replace("\\", "/")
FONT_BOLD = os.path.join(_FD, "DejaVuSans-Bold.ttf").replace("\\", "/")
getcontext().prec = 20

# ==========================================
# 0. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР
# ==========================================
def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args): pass
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
# 2. БАЗА ID (Telegram)
# ==========================================
LOCAL_DB = {100000000: "2013-08", 500000000: "2015-04", 1000000000: "2016-04", 2000000000: "2017-07", 3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01", 6000000000: "2021-02", 7000000000: "2022-06", 8000000000: "2024-06"}
MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

def estimate_registration(user_id):
    ids = sorted(LOCAL_DB.keys())
    if user_id <= ids[0]: p = LOCAL_DB[ids[0]].split("-"); return int(p[0]), int(p[1])
    if user_id >= ids[-1]: p = LOCAL_DB[ids[-1]].split("-"); return int(p[0]), int(p[1])
    for i in range(len(ids) - 1):
        if ids[i] <= user_id <= ids[i+1]:
            dt1 = datetime.strptime(LOCAL_DB[ids[i]], "%Y-%m")
            dt2 = datetime.strptime(LOCAL_DB[ids[i+1]], "%Y-%m")
            m_diff = (dt2.year - dt1.year) * 12 + (dt2.month - dt1.month)
            ratio = (user_id - ids[i]) / (ids[i+1] - ids[i])
            m_add = int(ratio * m_diff)
            y = dt1.year + (dt1.month + m_add - 1) // 12
            m = (dt1.month + m_add - 1) % 12 + 1
            return y, m
    return None, None

# ==========================================
# 3. АНАЛИЗ TON-КОШЕЛЬКА
# ==========================================
async def analyze_ton_wallet_max(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Курс TON
            ton_price = 0.0
            async with session.get("https://tonapi.io/v2/rates?tokens=ton&currencies=usd", headers=headers) as r:
                if r.status == 200:
                    rates = await r.json()
                    ton_price = rates.get("rates", {}).get("TON", {}).get("prices", {}).get("USD", 0.0)

            # 2. Базовая инфа
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200: return None, f"Кошелек не найден."
                data = await r.json()
                balance_dec = Decimal(int(data.get("balance", 0))) / Decimal(10**9)
                status = data.get("status", "unknown")
                interfaces = ", ".join(data.get("interfaces", [])) or "Нет"

            # 3. DNS (УЛУЧШЕННЫЙ ПОИСК)
            dns_name = "Неидентифицированные"
            try:
                # Пробуем backresolve
                async with session.get(f"{base_url}/accounts/{wallet_address}/dns/backresolve", headers=headers) as r:
                    if r.status == 200:
                        dns_data = await r.json()
                        if dns_data.get("names"):
                            dns_name = dns_data["names"][0]
                
                # Если пусто, ищем в списке истекающих
                if dns_name == "Неидентифицированные":
                    async with session.get(f"{base_url}/accounts/{wallet_address}/dns/expiring", headers=headers) as r:
                        if r.status == 200:
                            exp_data = await r.json()
                            items = exp_data.get("items", [])
                            if items:
                                dns_name = items[0].get("name", "Неидентифицированные")

                # Если всё еще пусто, сканируем NFT (домены TON — это NFT)
                if dns_name == "Неидентифицированные":
                    async with session.get(f"{base_url}/accounts/{wallet_address}/nfts", headers=headers) as r:
                        if r.status == 200:
                            nft_data = await r.json()
                            for item in nft_data.get("nft_items", []):
                                meta = item.get("metadata", {})
                                name = meta.get("name") or ""
                                if name.endswith(".ton"):
                                    dns_name = name
                                    break
            except Exception:
                pass

            # 4. Транзакции
            tx_list = []
            total_in = Decimal(0)
            total_out = Decimal(0)
            unique_peers = set()
            total_txs = 0
            timestamps = []
            
            senders_dict = {}
            receivers_dict = {}
            in_edges = []
            out_edges = []

            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=50", headers=headers) as r:
                if r.status == 200:
                    txs_data = await r.json()
                    for tx in txs_data.get("transactions", []):
                        utime = tx.get("utime", 0)
                        if utime > 0:
                            timestamps.append(utime)
                            date_str = datetime.fromtimestamp(utime).strftime("%d.%m.%Y")
                        else: date_str = "Неизвестно"

                        # Входящие
                        in_msg = tx.get("in_msg")
                        if in_msg and in_msg.get("value"):
                            amt = Decimal(int(in_msg.get("value", 0))) / Decimal(10**9)
                            if amt > 0:
                                total_in += amt
                                total_txs += 1
                                src = in_msg.get("source", {}).get("address", "?")
                                if src and src != "?":
                                    unique_peers.add(src)
                                    if src not in senders_dict: senders_dict[src] = {'count': 0, 'sum': Decimal(0), 'last': date_str}
                                    senders_dict[src]['count'] += 1
                                    senders_dict[src]['sum'] += amt
                                    in_edges.append((src, amt))
                                src_short = src[:4] + ".." + src[-4:] if len(src) > 8 else src
                                tx_list.append({"type": "IN", "amount": f"{amt.normalize():f}", "peer": src_short, "date": date_str})

                        # Исходящие
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amt = Decimal(int(out.get("value", 0))) / Decimal(10**9)
                                if amt > 0:
                                    total_out += amt
                                    total_txs += 1
                                    dest = out.get("destination", {}).get("address", "?")
                                    if dest and dest != "?":
                                        unique_peers.add(dest)
                                        if dest not in receivers_dict: receivers_dict[dest] = {'count': 0, 'sum': Decimal(0), 'last': date_str}
                                        receivers_dict[dest]['count'] += 1
                                        receivers_dict[dest]['sum'] += amt
                                        out_edges.append((dest, amt))
                                    dest_short = dest[:4] + ".." + dest[-4:] if len(dest) > 8 else dest
                                    tx_list.append({"type": "OUT", "amount": f"{amt.normalize():f}", "peer": dest_short, "date": date_str})

            usd_in = float(total_in) * ton_price
            usd_out = float(total_out) * ton_price

            first_date = datetime.fromtimestamp(min(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            last_date = datetime.fromtimestamp(max(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            check_time = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M:%S")

            top_senders = sorted(senders_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:3]
            top_receivers = sorted(receivers_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:3]

            report_data = {
                "wallet": wallet_address, "dns": dns_name, "status": status, "interfaces": interfaces,
                "balance": f"{balance_dec.normalize():f}", "balance_usd": f"${float(balance_dec) * ton_price:.4f}",
                "usd_in": f"${usd_in:.2f}", "usd_out": f"${usd_out:.2f}",
                "total_in": f"{total_in.normalize():f}", "total_out": f"{total_out.normalize():f}",
                "peers_count": len(unique_peers), "first_tx": first_date, "last_tx": last_date, "check_time": check_time,
                "txs": tx_list[:10], "top_senders": top_senders, "top_receivers": top_receivers,
                "in_edges": in_edges, "out_edges": out_edges
            }
            return report_data, None

    except Exception as e:
        return None, f"Ошибка API: {e}"

# ==========================================
# 4. РИСУЕМ ГРАФ
# ==========================================
def generate_graph_image(wallet, in_edges, out_edges):
    plt.figure(figsize=(10, 6), facecolor='white')
    ax = plt.gca()
    ax.axis('off')

    center_x, center_y = 0, 0
    top_in = sorted(in_edges, key=lambda x: x[1], reverse=True)[:5]
    top_out = sorted(out_edges, key=lambda x: x[1], reverse=True)[:5]

    in_y_coords = [i for i in range(-len(top_in), len(top_in)+1) if i != 0]
    out_y_coords = [i for i in range(-len(top_out), len(top_out)+1) if i != 0]

    for i, (src, amt) in enumerate(top_in):
        if i < len(in_y_coords):
            y_pos = in_y_coords[i]
            plt.scatter([-4], [y_pos], s=100, color='gray', zorder=5)
            plt.text(-4.2, y_pos, src[:4]+".."+src[-4:], ha='right', va='center', fontsize=7)
            plt.annotate("", xy=(center_x, center_y), xytext=(-4, y_pos),
                         arrowprops=dict(arrowstyle="->", color="gray", alpha=0.7))
            mid_x, mid_y = (-4 + center_x)/2, (y_pos + center_y)/2
            plt.text(mid_x, mid_y, f"{amt:.1f} TON", fontsize=6, ha='center', va='bottom', color='black')

    for i, (dest, amt) in enumerate(top_out):
        if i < len(out_y_coords):
            y_pos = out_y_coords[i]
            plt.scatter([4], [y_pos], s=100, color='gray', zorder=5)
            plt.text(4.2, y_pos, dest[:4]+".."+dest[-4:], ha='left', va='center', fontsize=7)
            plt.annotate("", xy=(4, y_pos), xytext=(center_x, center_y),
                         arrowprops=dict(arrowstyle="->", color="gray", alpha=0.7))
            mid_x, mid_y = (4 + center_x)/2, (y_pos + center_y)/2
            plt.text(mid_x, mid_y, f"{amt:.1f} TON", fontsize=6, ha='center', va='bottom', color='black')

    plt.scatter([center_x], [center_y], s=300, color='#007bff', zorder=6)
    plt.text(center_x, center_y - 0.3, "Проверяемый\nадрес", ha='center', va='top', fontsize=8, color='black', weight='bold')
    plt.text(center_x, center_y + 0.3, wallet[:4]+".."+wallet[-4:], ha='center', va='bottom', fontsize=7, color='white', zorder=7)

    plt.xlim(-6, 6)
    plt.ylim(-6, 6)

    img_buffer = io.BytesIO()
    plt.savefig(img_buffer, format='png', bbox_inches='tight', dpi=200)
    plt.close()
    img_buffer.seek(0)
    return img_buffer

# ==========================================
# 5. ГЕНЕРАЦИЯ PDF
# ==========================================
def generate_aml_pdf(data: dict, graph_img):
    tx_rows = ""
    for tx in data['txs']:
        color = "#28a745" if tx['type'] == "IN" else "#dc3545"
        sign = "+" if tx['type'] == "IN" else "-"
        tx_rows += f"<tr><td style='color:{color}; font-weight:bold;'>[{tx['type']}]</td><td>{sign}{tx['amount']} TON</td><td>{tx['peer']}</td><td>{tx['date']}</td></tr>"

    graph_html = ""
    if graph_img:
        img_b64 = base64.b64encode(graph_img.getvalue()).decode('utf-8')
        graph_html = f'<div style="text-align:center; margin-bottom:10px;"><img src="data:image/png;base64,{img_b64}" style="max-width: 100%;" /></div>'

    senders_html = ""
    if data['top_senders']:
        senders_html += "<table class='peers-table'><thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th></tr></thead><tbody>"
        for addr, info in data['top_senders']:
            senders_html += f"<tr><td>{addr[:6]}...{addr[-4:]}</td><td>—</td><td>{info['count']}</td><td>{info['sum'].normalize():f} TON</td></tr>"
        senders_html += "</tbody></table>"

    receivers_html = ""
    if data['top_receivers']:
        receivers_html += "<table class='peers-table'><thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th></tr></thead><tbody>"
        for addr, info in data['top_receivers']:
            receivers_html += f"<tr><td>{addr[:6]}...{addr[-4:]}</td><td>—</td><td>{info['count']}</td><td>{info['sum'].normalize():f} TON</td></tr>"
        receivers_html += "</tbody></table>"

    html = f"""
    <html>
    <head>
        <style>
            @font-face {{ font-family: 'DejaVu'; src: url('{FONT_REG}'); }}
            @font-face {{ font-family: 'DejaVu'; src: url('{FONT_BOLD}'); font-weight: bold; }}
            @page {{ size: A4; margin: 1cm; }}
            body {{ font-family: 'DejaVu'; color: #333; font-size: 9pt; }}
            
            .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
            .header-table td {{ border: 1px solid #ddd; padding: 6px; vertical-align: top; }}
            .header-table td.label {{ background-color: #f8f9fa; font-weight: bold; width: 35%; color: #555; }}
            
            .section-title {{ background-color: #2c3e50; color: white; padding: 8px; font-weight: bold; margin-top: 20px; margin-bottom: 10px; font-size: 11pt; }}
            
            .tx-table, .peers-table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
            .tx-table th, .peers-table th {{ background-color: #f1f1f1; padding: 6px; text-align: left; border-bottom: 2px solid #ddd; font-size: 8pt; }}
            .tx-table td, .peers-table td {{ padding: 6px; border-bottom: 1px solid #eee; font-size: 8pt; }}
            
            .legend {{ font-size: 8pt; color: #555; margin-top: 5px; text-align: left; }}
        </style>
    </head>
    <body>
        <h1 style="text-align:center; color:#2c3e50; font-size: 16pt;">AML-отчет по криптоадресу</h1>

        <table class="header-table">
            <tr><td class="label">Дата и время проверки</td><td>{data['check_time']} (Europe/Minsk)</td></tr>
            <tr><td class="label">Адрес кошелька</td><td>{data['wallet']}</td></tr>
            <tr><td class="label">Сеть</td><td>TON</td></tr>
            <tr><td class="label">Обозреватель</td><td>https://tonviewer.com/{data['wallet']}</td></tr>
            <tr><td class="label">Метка адреса</td><td>{data['dns']} — Неидентифицированные</td></tr>
            <tr><td class="label">Тип адреса</td><td>кошелёк</td></tr>
            <tr><td class="label">Текущий баланс</td><td><b>{data['balance']} TON</b> ≈ {data['balance_usd']}</td></tr>
            <tr><td class="label">Оборот в USD</td><td>вход ≈ {data['usd_in']} · выход ≈ {data['usd_out']}</td></tr>
            <tr><td class="label">Первая / последняя</td><td>{data['first_tx']} / {data['last_tx']}</td></tr>
            <tr><td class="label">Уникальных контрагентов</td><td>{data['peers_count']}</td></tr>
            <tr><td class="label">Период анализа</td><td>всё время</td></tr>
            <tr><td class="label">Мелкие переводы</td><td>скрыты (порог: 0.1 TON)</td></tr>
        </table>

        <div class="section-title">1. Цепочка переводов</div>
        {graph_html}
        <div class="legend">
            ● синий — проверяемый адрес; ● серый — неидентифицированный. Стрелки — направление движения средств, подписи — суммы.
        </div>

        <div class="section-title">2. Крупнейшие отправители (топ-3)</div>
        {senders_html if senders_html else "<p>Нет данных</p>"}

        <div class="section-title">3. Крупнейшие получатели (топ-3)</div>
        {receivers_html if receivers_html else "<p>Нет данных</p>"}

        <div class="section-title">4. Последние транзакции</div>
        <table class="tx-table">
            <thead><tr><th>Тип</th><th>Сумма</th><th>Контрагент</th><th>Дата и время</th></tr></thead>
            <tbody>{tx_rows}</tbody>
        </table>
    </body>
    </html>
    """
    
    pdf_buffer = io.BytesIO()
    pisa.CreatePDF(html.encode('utf-8'), dest=pdf_buffer, encoding='utf-8')
    pdf_buffer.seek(0)
    return pdf_buffer

# ==========================================
# 6. КЛИЕНТЫ И ОБРАБОТЧИКИ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)
last_request_time = {}

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply("👋 Привет! Отправь мне:\n1. <b>@username</b> (профиль TG)\n2. <b>TON-адрес</b> (AML-отчет с графом).")

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

    if target_input.startswith(("UQ", "EQ", "0:")):
        status_msg = await message.reply("🔍 Генерирую AML-отчет с графом...")
        
        report_data, error = await analyze_ton_wallet_max(target_input)
        if error:
            await status_msg.edit_text(error)
            return
            
        graph_img = generate_graph_image(target_input, report_data['in_edges'], report_data['out_edges'])
        pdf_buffer = generate_aml_pdf(report_data, graph_img)
        
        await message.reply_document(
            document=pdf_buffer,
            file_name=f"AML_Report_{target_input[:6]}.pdf",
            caption=f"🛡 AML-Анализ с графом связей\n`{target_input}`"
        )
        await status_msg.delete()
        return

    if target_input.isdigit():
        await message.reply("⚠️ Поиск по ID отключен.")
        return
    if not target_input.startswith('@'): target_input = f"@{target_input}"
    
    status_msg = await message.reply(f"🔍 Ищу {target_input}...")
    try:
        user = await user_app.get_users(target_input)
        if isinstance(user, list): user = user[0]
        user_id = user.id
        name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
        card = f"🪪 <b>Инфо</b>\n\n<b>ID:</b> <code>{user_id}</code>\n<b>Имя:</b> {name}\n"
        await status_msg.edit_text(card)
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    print("🤖 Запуск...")
    user_app.start()
    bot.run()
