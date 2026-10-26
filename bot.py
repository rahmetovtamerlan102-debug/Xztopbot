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
import asyncio
import textwrap
import matplotlib.pyplot as plt
import matplotlib
from decimal import Decimal, getcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timezone, timedelta
from xhtml2pdf import pisa
import base64
from tonsdk.utils import Address

# --- ФИКС ШРИФТОВ ---
_FD = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
FONT_REG = os.path.join(_FD, "DejaVuSans.ttf").replace("\\", "/")
FONT_BOLD = os.path.join(_FD, "DejaVuSans-Bold.ttf").replace("\\", "/")
getcontext().prec = 20

# --- КОНВЕРТАЦИЯ АДРЕСОВ (ВСЕГДА В UQ) ---
def to_friendly(addr):
    if not addr or addr == "?": return "?"
    try:
        return Address(addr).to_string(is_user_friendly=True, is_bounceable=False)
    except Exception:
        return addr

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
# 3. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
async def fetch_counterparties(session, headers, addr, limit=30):
    ins, outs = {}, {}
    try:
        url = f"https://tonapi.io/v2/blockchain/accounts/{addr}/transactions?limit={limit}"
        async with session.get(url, headers=headers) as r:
            if r.status != 200: return [], []
            for tx in (await r.json()).get("transactions", []):
                m = tx.get("in_msg") or {}
                s = (m.get("source") or {}).get("address")
                if s and m.get("value"):
                    f_s = to_friendly(s)
                    ins[f_s] = ins.get(f_s, 0) + int(m["value"]) / 1e9
                for o in tx.get("out_msgs", []):
                    d = (o.get("destination") or {}).get("address")
                    if d and o.get("value"):
                        f_d = to_friendly(d)
                        outs[f_d] = outs.get(f_d, 0) + int(o["value"]) / 1e9
    except Exception: pass
    top = lambda d: sorted(d.items(), key=lambda x: x[1], reverse=True)[:3]
    return top(ins), top(outs)

async def fetch_names(session, headers, addrs):
    names = {}
    if not addrs: return names
    try:
        async with session.post("https://tonapi.io/v2/accounts/_bulk", headers=headers, json={"account_ids": list(addrs)}) as r:
            if r.status == 200:
                for a in (await r.json()).get("accounts", []):
                    names[to_friendly(a["address"])] = a.get("name")
    except Exception: pass
    return names

def _color(name):
    n = (name or "").lower()
    if any(k in n for k in ("mixer", "scam", "sanction")): return "#fd7e14"
    if any(k in n for k in ("binance", "okx", "bybit", "kucoin", "mexc", "bitget", "htx", "exchange")): return "#28a745"
    if any(k in n for k in ("ston", "dedust", "dex", "swap", "bridge")): return "#d4a017"
    return "#888888"

# ==========================================
# 4. РИСУЕМ ГРАФ
# ==========================================
def generate_graph_image(wallet, data):
    names = data["names"]
    
    max_slots = max(
        sum(max(1, len(data.get("in2", {}).get(a, []))) for a, _ in data["top_senders"]),
        sum(max(1, len(data.get("out2", {}).get(a, []))) for a, _ in data["top_receivers"]),
        4 
    )
    
    fig_height = max(8, max_slots * 1.5)
    fig, ax = plt.subplots(figsize=(15, fig_height), facecolor='white')
    ax.axis("off")

    def arrow(p, q, text=""):
        ax.annotate("", xy=q, xytext=p, arrowprops=dict(
            arrowstyle="->", color="#777", lw=0.8, shrinkA=5, shrinkB=5))
        if text:
            ax.text((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, text,
                    fontsize=5, ha="center", va="bottom", color="#555",
                    bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.8))

    def node(x, y, addr, size=60):
        nm = names.get(addr)
        ax.scatter([x], [y], s=size, color=_color(nm), zorder=5)
        wrapped = '\n'.join(textwrap.wrap(addr, 10))
        label = wrapped + (f"\n{nm}" if nm else "")
        ax.text(x, y - 0.25, label, ha="center", va="top", fontsize=4.5, color="#333")

    total_sender_slots = sum(max(1, len(data.get("in2", {}).get(a, []))) for a, _ in data["top_senders"])
    y_start = total_sender_slots / 2
    current_y = y_start

    for addr, info in data["top_senders"]:
        kids = data.get("in2", {}).get(addr, [])
        num_slots = max(1, len(kids))
        
        y_pos = current_y - (num_slots / 2) + 0.5
        p_sender = (-3, y_pos)
        
        node(-3, y_pos, addr, size=80)
        arrow(p_sender, (0, 0), text=f"{info['sum']:.2f} TON")

        for i, (kid_addr, kid_amt) in enumerate(kids):
            y_kid = current_y - i - 0.5
            k = (-6, y_kid)
            arrow(k, p_sender, text=f"{kid_amt:.2f} TON")
            node(-6, y_kid, kid_addr, size=40)

        current_y -= num_slots + 0.5 

    total_recv_slots = sum(max(1, len(data.get("out2", {}).get(a, []))) for a, _ in data["top_receivers"])
    current_y = total_recv_slots / 2

    for addr, info in data["top_receivers"]:
        kids = data.get("out2", {}).get(addr, [])
        num_slots = max(1, len(kids))
        
        y_pos = current_y - (num_slots / 2) + 0.5
        p_recv = (3, y_pos)

        node(3, y_pos, addr, size=80)
        arrow((0, 0), p_recv, text=f"{info['sum']:.2f} TON")

        for i, (kid_addr, kid_amt) in enumerate(kids):
            y_kid = current_y - i - 0.5
            k = (6, y_kid)
            arrow(p_recv, k, text=f"{kid_amt:.2f} TON")
            node(6, y_kid, kid_addr, size=40)

        current_y -= num_slots + 0.5

    ax.scatter([0], [0], s=250, color="#1a56db", zorder=6)
    wrapped_wallet = '\n'.join(textwrap.wrap(wallet, 10))
    ax.text(0, -0.3, f"**{wrapped_wallet}**\n{data['dns']}", ha="center", va="top", fontsize=5.5, weight="bold")

    top_y = max(y_start, total_recv_slots / 2) + 2
    for x, t in zip([-6, -3, 0, 3, 6], ("Источники\n2-го шага", "Отправители", "Проверяемый\nадрес", "Получатели", "Получатели\n2-го шага")):
        ax.text(x, top_y, t, ha="center", va="bottom", fontsize=8, weight="bold")

    ax.set_xlim(-8, 8)
    ax.set_ylim(-top_y, top_y)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=200, facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return buf

# ==========================================
# 5. АНАЛИЗ TON-КОШЕЛЬКА
# ==========================================
async def analyze_ton_wallet_max(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            ton_price = 0.0
            async with session.get("https://tonapi.io/v2/rates?tokens=ton&currencies=usd", headers=headers) as r:
                if r.status == 200:
                    rates = await r.json()
                    ton_price = rates.get("rates", {}).get("TON", {}).get("prices", {}).get("USD", 0.0)

            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200: return None, f"Кошелек не найден."
                data = await r.json()
                balance_dec = Decimal(int(data.get("balance", 0))) / Decimal(10**9)
                status = data.get("status", "unknown")
                interfaces = ", ".join(data.get("interfaces", [])) or "Нет"

            dns_name = "Неидентифицированные"
            try:
                async with session.get(f"{base_url}/accounts/{wallet_address}/dns/backresolve", headers=headers) as r:
                    if r.status == 200:
                        dns_data = await r.json()
                        if dns_data.get("names"): dns_name = dns_data["names"][0]
            except Exception: pass

            tx_list = []
            total_in = Decimal(0)
            total_out = Decimal(0)
            unique_peers = set()
            total_txs = 0
            timestamps = []
            senders_dict, receivers_dict = {}, {}

            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=100", headers=headers) as r:
                if r.status == 200:
                    txs_data = await r.json()
                    for tx in txs_data.get("transactions", []):
                        utime = tx.get("utime", 0)
                        if utime > 0:
                            timestamps.append(utime)
                            date_str = datetime.fromtimestamp(utime).strftime("%d.%m.%Y")
                        else: date_str = "Неизвестно"

                        in_msg = tx.get("in_msg")
                        if in_msg and in_msg.get("value"):
                            amt = Decimal(int(in_msg.get("value", 0))) / Decimal(10**9)
                            if amt >= Decimal('0.1'): 
                                total_in += amt; total_txs += 1
                                src = to_friendly(in_msg.get("source", {}).get("address", "?"))
                                if src and src != "?":
                                    unique_peers.add(src)
                                    if src not in senders_dict: senders_dict[src] = {'count': 0, 'sum': Decimal(0), 'last': date_str}
                                    senders_dict[src]['count'] += 1; senders_dict[src]['sum'] += amt; senders_dict[src]['last'] = date_str
                                src_short = src[:4] + ".." + src[-4:] if len(src) > 8 else src
                                tx_list.append({"type": "IN", "amount": f"{amt.normalize():f}", "peer": src_short, "date": date_str})

                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amt = Decimal(int(out.get("value", 0))) / Decimal(10**9)
                                if amt >= Decimal('0.1'):
                                    total_out += amt; total_txs += 1
                                    dest = to_friendly(out.get("destination", {}).get("address", "?"))
                                    if dest and dest != "?":
                                        unique_peers.add(dest)
                                        if dest not in receivers_dict: receivers_dict[dest] = {'count': 0, 'sum': Decimal(0), 'last': date_str}
                                        receivers_dict[dest]['count'] += 1; receivers_dict[dest]['sum'] += amt; receivers_dict[dest]['last'] = date_str
                                    dest_short = dest[:4] + ".." + dest[-4:] if len(dest) > 8 else dest
                                    tx_list.append({"type": "OUT", "amount": f"{amt.normalize():f}", "peer": dest_short, "date": date_str})

            top_senders = sorted(senders_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            top_receivers = sorted(receivers_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]

            in2, out2 = {}, {}
            for a, _ in top_senders[:3]:
                ins, _o = await fetch_counterparties(session, headers, a)
                in2[a] = ins
                await asyncio.sleep(1.1)
            for a, _ in top_receivers[:3]:
                _i, outs = await fetch_counterparties(session, headers, a)
                out2[a] = outs
                await asyncio.sleep(1.1)

            all_addrs = {a for a, _ in top_senders} | {a for a, _ in top_receivers}
            for lst in list(in2.values()) + list(out2.values()):
                all_addrs |= {a for a, _ in lst}
            names = await fetch_names(session, headers, all_addrs)

            usd_in = float(total_in) * ton_price; usd_out = float(total_out) * ton_price
            first_date = datetime.fromtimestamp(min(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            last_date = datetime.fromtimestamp(max(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            check_time = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M:%S")

            report_data = {
                "wallet": wallet_address, "dns": dns_name, "status": status, "interfaces": interfaces,
                "balance": f"{balance_dec.normalize():f}", "balance_usd": f"${float(balance_dec) * ton_price:.4f}",
                "usd_in": f"${usd_in:.2f}", "usd_out": f"${usd_out:.2f}", "ton_price": ton_price,
                "total_in": f"{total_in.normalize():f}", "total_out": f"{total_out.normalize():f}",
                "peers_count": len(unique_peers), "first_tx": first_date, "last_tx": last_date, "check_time": check_time,
                "txs": tx_list[:10], 
                "top_senders": top_senders, 
                "top_receivers": top_receivers,
                "in2": in2, "out2": out2, "names": names
            }
            return report_data, None

    except Exception as e:
        return None, f"Ошибка API: {e}"

# ==========================================
# 6. ГЕНЕРАЦИЯ PDF
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
        graph_html = f'<div style="text-align:center; margin-bottom:10px;"><img src="data:image/png;base64,{img_b64}" width="700" /></div>'

    # --- ТАБЛИЦА ОТПРАВИТЕЛЕЙ ---
    senders_html = ""
    if data['top_senders']:
        senders_html += f'<div style="background-color:#d9d9d9; padding:5px; font-weight:bold; font-size:10pt; margin-top:15px; margin-bottom:5px;">Отправители</div>'
        senders_html += "<table class='peers-table'><thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th><th>Период</th></tr></thead><tbody>"
        
        for addr, info in data['top_senders']:
            label = data['names'].get(addr) or "—"
            usd_sum = float(info['sum']) * data['ton_price']
            senders_html += f"<tr><td style='word-break: break-all; width: 40%;'>{addr}</td><td>{label}</td><td>{info['count']}</td><td><b>{info['sum'].normalize():f} TON</b><br><span style='color:#777;'>≈ ${usd_sum:.2f}</span></td><td>{info['last']}</td></tr>"
        senders_html += "</tbody></table>"

    # --- ТАБЛИЦА ПОЛУЧАТЕЛЕЙ ---
    receivers_html = ""
    if data['top_receivers']:
        receivers_html += f'<div style="background-color:#d9d9d9; padding:5px; font-weight:bold; font-size:10pt; margin-top:15px; margin-bottom:5px;">Получатели</div>'
        receivers_html += "<table class='peers-table'><thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th><th>Период</th></tr></thead><tbody>"
        
        for addr, info in data['top_receivers']:
            label = data['names'].get(addr) or "—"
            usd_sum = float(info['sum']) * data['ton_price']
            receivers_html += f"<tr><td style='word-break: break-all; width: 40%;'>{addr}</td><td>{label}</td><td>{info['count']}</td><td><b>{info['sum'].normalize():f} TON</b><br><span style='color:#777;'>≈ ${usd_sum:.2f}</span></td><td>{info['last']}</td></tr>"
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
            .tx-table th, .peers-table th {{ background-color: #f1f1f1; padding: 6px; text-align: left; border-bottom: 2px solid #ccc; font-size: 8pt; color: #333; }}
            .tx-table td, .peers-table td {{ padding: 6px; border-bottom: 1px solid #eee; font-size: 8pt; vertical-align: top; }}
            
            .legend {{ font-size: 8pt; color: #555; margin-top: 5px; text-align: left; }}
        </style>
    </head>
    <body>
        <h1 style="text-align:center; color:#2c3e50; font-size: 16pt;">AML-отчет по криптоадресу</h1>

        <table class="header-table">
            <tr><td class="label">Дата и время проверки</td><td>{data['check_time']} (Europe/Minsk)</td></tr>
            <tr><td class="label">Адрес кошелька</td><td>{data['wallet']}</td></tr>
            <tr><td class="label">Сеть</td><td>TON</td></tr>
            <tr><td class="label">Обозреватель</td><td>tonviewer.com/{data['wallet']}</td></tr>
            <tr><td class="label">Метка адреса</td><td>{data['dns']} — Неидентифицированные</td></tr>
            <tr><td class="label">Тип адреса</td><td>кошелёк</td></tr>
            <tr><td class="label">Текущий баланс</td><td><b>{data['balance']} TON</b> ≈ {data['balance_usd']}</td></tr>
            <tr><td class="label">Оборот в USD</td><td>вход ≈ {data['usd_in']} · выход ≈ {data['usd_out']}</td></tr>
            <tr><td class="label">Первая / последняя</td><td>{data['first_tx']} / {data['last_tx']}</td></tr>
            <tr><td class="label">Уникальных контрагентов</td><td>{data['peers_count']}</td></tr>
            <tr><td class="label">Период анализа</td><td>всё время</td></tr>
            <tr><td class="label">Мелкие переводы</td><td>скрыты (порог: 0.1 TON)</td></tr>
        </table>

        <pdf:nextpage />

        <div class="section-title">1. Цепочка переводов</div>
        {graph_html}
        <div class="legend">
            ● синий — проверяемый адрес; ● зеленый — биржа; ● оранжевый — миксер/скам; ● желтый — DEX/обменник; ● серый — неидентифицированный.
        </div>

        {senders_html if senders_html else ""}
        {receivers_html if receivers_html else ""}

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
# 7. КЛИЕНТЫ И ОБРАБОТЧИКИ
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
        
        target_input = to_friendly(target_input)
        
        report_data, error = await analyze_ton_wallet_max(target_input)
        if error:
            await status_msg.edit_text(error)
            return
            
        graph_img = generate_graph_image(target_input, report_data)
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
