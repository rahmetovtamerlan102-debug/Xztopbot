import os
import sys
import asyncio
import io
import base64
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
from decimal import Decimal, getcontext
from xhtml2pdf import pisa

import aiohttp
from tonsdk.utils import Address

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ==========================================
# КОНФИГУРАЦИЯ (Только токен бота)
# ==========================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
TON_API_KEY = os.environ.get("TON_API_KEY", "")
getcontext().prec = 20

# Шрифты для PDF
_FD = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
FONT_REG = os.path.join(_FD, "DejaVuSans.ttf").replace("\\", "/")
FONT_BOLD = os.path.join(_FD, "DejaVuSans-Bold.ttf").replace("\\", "/")

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def to_friendly(addr):
    if not addr or addr == "?": return "?"
    try: return Address(addr).to_string(is_user_friendly=True, is_bounceable=False).replace('/', '_').replace('+', '-')
    except: return addr

# ==========================================
# АНАЛИЗ TON (Тот же код)
# ==========================================
async def analyze_ton_wallet_full(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"
    try:
        wallet_address = to_friendly(wallet_address)
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200: return None, "Кошелек не найден"
                acc = await r.json()
                balance = Decimal(int(acc.get("balance", 0))) / Decimal(10**9)
                status = acc.get("status", "unknown")
            
            dns_name = "Неидентифицированные"
            nft_stats = {"recv": 0, "mint": 0, "sent": 0, "spam": 0, "collections": {}}
            async with session.get(f"{base_url}/accounts/{wallet_address}/nfts?limit=100", headers=headers) as r:
                if r.status == 200:
                    for item in (await r.json()).get("nft_items", []):
                        name = (item.get("metadata") or {}).get("name") or ""
                        if name.endswith(".ton"):
                            dns_name = name
                            continue
                        if item.get("trust") == "blacklist": nft_stats["spam"] += 1
                        else:
                            coll = (item.get("collection") or {}).get("name")
                            if coll: nft_stats["collections"][coll] = nft_stats["collections"].get(coll, 0) + 1

            events = []
            total_in, total_out = Decimal(0), Decimal(0)
            senders_dict, receivers_dict = {}, {}
            first_tx, last_tx = 0, 0
            
            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=100", headers=headers) as r:
                if r.status == 200:
                    txs = (await r.json()).get("transactions", [])
                    if txs:
                        times = [tx.get("utime", 0) for tx in txs if tx.get("utime", 0) > 0]
                        if times:
                            first_tx = min(times)
                            last_tx = max(times)

                    for tx in txs:
                        utime = tx.get("utime", 0)
                        if tx.get("in_msg") and tx["in_msg"].get("value"):
                            amt = Decimal(int(tx["in_msg"]["value"])) / Decimal(10**9)
                            if amt >= Decimal('0.1'):
                                total_in += amt
                                src = to_friendly(tx["in_msg"].get("source", {}).get("address", "?"))
                                if src != "?":
                                    events.append((utime, "IN", float(amt), src))
                                    if src not in senders_dict: senders_dict[src] = {'count': 0, 'sum': Decimal(0), 'last': utime}
                                    senders_dict[src]['count'] += 1
                                    senders_dict[src]['sum'] += amt
                                    senders_dict[src]['last'] = utime
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amt = Decimal(int(out["value"])) / Decimal(10**9)
                                if amt >= Decimal('0.1'):
                                    total_out += amt
                                    dst = to_friendly(out.get("destination", {}).get("address", "?"))
                                    if dst != "?":
                                        events.append((utime, "OUT", float(amt), dst))
                                        if dst not in receivers_dict: receivers_dict[dst] = {'count': 0, 'sum': Decimal(0), 'last': utime}
                                        receivers_dict[dst]['count'] += 1
                                        receivers_dict[dst]['sum'] += amt
                                        receivers_dict[dst]['last'] = utime

            top_senders = sorted(senders_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            top_receivers = sorted(receivers_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]

            senders_api = []
            for a, d in top_senders:
                senders_api.append({"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—", "count": d['count']})
                
            receivers_api = []
            for a, d in top_receivers:
                receivers_api.append({"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—", "count": d['count']})

            out_pct = (float(total_out) / float(total_in) * 100) if total_in > 0 else 0
            profile_type = "«Транзитка» (адрес-прокладка)" if out_pct >= 90 else "Личный кошелёк"
            profile_conf = "средняя"
            profile_desc = "Адрес-прокладка: почти всё поступившее быстро уходит дальше." if out_pct >= 90 else "Средства остаются на адресе."

            return {
                "wallet": wallet_address, "balance": f"{balance.normalize():f}", "status": status, "dns": dns_name,
                "total_in": f"{total_in.normalize():f}", "total_out": f"{total_out.normalize():f}",
                "senders": senders_api, "receivers": receivers_api, "nft": nft_stats,
                "first_tx": datetime.fromtimestamp(first_tx).strftime("%d.%m.%Y %H:%M") if first_tx else "Нет",
                "last_tx": datetime.fromtimestamp(last_tx).strftime("%d.%m.%Y %H:%M") if last_tx else "Нет",
                "profile": {"type": profile_type, "conf": profile_conf, "desc": profile_desc, "ops": len(events), "peers": len(senders_dict)+len(receivers_dict)}
            }, None
    except Exception as e:
        return None, str(e)

# ==========================================
# ГРАФ И PDF (Тот же код)
# ==========================================
def generate_graph(wallet, senders, receivers):
    G = nx.DiGraph()
    sw = wallet[:6] + "..." + wallet[-4:]
    G.add_node(sw, color='#007aff', size=2000)
    
    for s in senders:
        G.add_edge(str(s['addr'])[:6]+"..."+str(s['addr'])[-4:], sw, label=f"{s['amount']} TON", color='#4dcd5e')
    for r in receivers:
        G.add_edge(sw, str(r['addr'])[:6]+"..."+str(r['addr'])[-4:], label=f"{r['amount']} TON", color='#e85d5d')
        
    if len(G.nodes) <= 1: return None
    
    plt.figure(figsize=(10, 8))
    pos = nx.spring_layout(G, k=1, seed=42)
    nx.draw_networkx_nodes(G, pos, node_color=[G.nodes[n].get('color', '#888') for n in G.nodes], node_size=1500)
    nx.draw_networkx_edges(G, pos, edge_color=[G.edges[e].get('color', '#888') for e in G.edges], width=2, arrowsize=20)
    nx.draw_networkx_labels(G, pos, font_size=8, font_weight='bold')
    nx.draw_networkx_edge_labels(G, pos, edge_labels=nx.get_edge_attributes(G, 'label'), font_size=7)
    plt.axis('off')
    
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=100)
    plt.close()
    buf.seek(0)
    return buf

def generate_pdf(data, graph_img):
    graph_html = f'<div style="text-align:center; margin-bottom:10px;"><img src="data:image/png;base64,{base64.b64encode(graph_img.getvalue()).decode("utf-8")}" width="600" /></div>' if graph_img else ""
    
    senders_html = "".join(f"<tr><td>{s['addr']}</td><td>{s['label']}</td><td>{s['count']}</td><td><b>{s['amount']} TON</b></td><td>{s['date']}</td></tr>" for s in data['senders'])
    receivers_html = "".join(f"<tr><td>{r['addr']}</td><td>{r['label']}</td><td>{r['count']}</td><td><b>{r['amount']} TON</b></td><td>{r['date']}</td></tr>" for r in data['receivers'])
    
    nf = data['nft']
    coll = ", ".join(f"{k} ({v})" for k, v in nf['collections'].items()) or "нет"
    p = data['profile']

    html = f"""<html><head><style>
        @font-face {{ font-family: 'DejaVu'; src: url('{FONT_REG}'); }}
        @page {{ size: A4; margin: 1cm; }} body {{ font-family: 'DejaVu'; color: #333; font-size: 9pt; }}
        .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
        .header-table td {{ border: 1px solid #ddd; padding: 6px; vertical-align: top; }}
        .header-table td.label {{ background-color: #f8f9fa; font-weight: bold; width: 35%; color: #555; }}
        .section-title {{ color: #1a4fa0; font-weight: bold; font-size: 14pt; margin-top: 18px; margin-bottom: 6px; }}
        .peers-table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
        .peers-table th {{ background-color: #f1f1f1; padding: 6px; text-align: left; border-bottom: 2px solid #ccc; font-size: 8pt; color: #333; }}
        .peers-table td {{ padding: 6px; border-bottom: 1px solid #eee; font-size: 8pt; vertical-align: top; }}
        .small {{ font-size: 8pt; color: #555; }}
    </style></head><body>
        <h1 style="text-align:center; color:#2c3e50; font-size: 16pt;">AML-отчет по криптоадресу</h1>
        
        <table class="header-table">
            <tr><td class="label">Дата и время проверки</td><td>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</td></tr>
            <tr><td class="label">Адрес кошелька</td><td>{data['wallet']}</td></tr>
            <tr><td class="label">Сеть</td><td>TON</td></tr>
            <tr><td class="label">Метка адреса</td><td>{data['dns']}</td></tr>
            <tr><td class="label">Текущий баланс</td><td><b>{data['balance']} TON</b></td></tr>
            <tr><td class="label">Оборот</td><td>вход {data['total_in']} TON / выход {data['total_out']} TON</td></tr>
            <tr><td class="label">Первая / последняя</td><td>{data['first_tx']} / {data['last_tx']}</td></tr>
            <tr><td class="label">Статус</td><td>{data['status']}</td></tr>
        </table>
        
        <div class="section-title">1. Цепочка переводов</div>
        {graph_html}
        
        <div class="section-title">2. Контрагенты</div>
        <p style="font-weight:bold;">Отправители:</p>
        <table class="peers-table">
            <thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th><th>Период</th></tr></thead>
            <tbody>{senders_html}</tbody>
        </table>
        
        <p style="font-weight:bold;">Получатели:</p>
        <table class="peers-table">
            <thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th><th>Период</th></tr></thead>
            <tbody>{receivers_html}</tbody>
        </table>
        
        <div class="section-title">3. NFT-операции</div>
        <p>Получено NFT: <b>{nf['recv']}</b>, выпущено на адрес: <b>{nf['mint']}</b>, отправлено: <b>{nf['sent']}</b>, спам-NFT: <b>{nf['spam']}</b>.</p>
        <p class="small">Коллекции: {coll}</p>
        
        <div class="section-title">4. Профиль владельца адреса</div>
        <p>Вероятный тип владельца: <b>{p['type']}</b> · уверенность <b>{p['conf']}</b>.</p>
        <p class="small">{p['desc']}</p>
        <p class="small">Операций: {p['ops']}, Контрагентов: {p['peers']}</p>
        
        <div class="section-title">5. Вывод</div>
        <table class="peers-table">
            <thead><tr><th>Актив</th><th>Поступило</th><th>Отправлено</th><th>Разница</th></tr></thead>
            <tbody>
                <tr><td><b>TON</b></td><td>{data['total_in']}</td><td>{data['total_out']}</td><td>{(Decimal(data['total_in']) - Decimal(data['total_out'])).normalize():f}</td></tr>
            </tbody>
        </table>
    </body></html>"""
    
    pdf_buffer = io.BytesIO()
    pisa.createPDF(html.encode('utf-8'), dest=pdf_buffer, encoding='utf-8')
    pdf_buffer.seek(0)
    return pdf_buffer

# ==========================================
# ОТПРАВКА СООБЩЕНИЙ ЧЕРЕЗ HTTP API
# ==========================================
async def send_message(chat_id, text):
    url = f"{TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as r:
            return await r.json()

async def send_document(chat_id, file_bytes, filename, caption):
    url = f"{TELEGRAM_API_URL}/sendDocument"
    data = aiohttp.FormData()
    data.add_field('chat_id', str(chat_id))
    data.add_field('caption', caption)
    data.add_field('document', file_bytes, filename=filename, content_type='application/pdf')
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=data) as r:
            return await r.json()

# ==========================================
# ОСНОВНОЙ ЦИКЛ БОТА (Long Polling)
# ==========================================
async def main():
    print("🤖 Бот запущен (HTTP API)...")
    offset = 0
    headers = {"Accept": "application/json"}
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                url = f"{TELEGRAM_API_URL}/getUpdates?offset={offset}&timeout=30"
                async with session.get(url, headers=headers) as r:
                    data = await r.json()
                    
                if not data.get("ok"):
                    print(f"Ошибка API: {data}")
                    await asyncio.sleep(5)
                    continue
                    
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    
                    if "message" not in update or "text" not in update["message"]:
                        continue
                        
                    chat_id = update["message"]["chat"]["id"]
                    text = update["message"]["text"].strip()
                    
                    # Обработка /start
                    if text == "/start":
                        await send_message(chat_id, "👋 Отправь мне TON-адрес (UQ... или .ton), и я пришлю PDF-отчет.")
                        continue
                        
                    # Обработка TON-адреса
                    if not text.startswith(("UQ", "EQ", "0:")) and not text.endswith(".ton"):
                        await send_message(chat_id, "⚠️ Пожалуйста, отправьте TON-адрес (начинается на UQ/EQ/0:) или .ton домен.")
                        continue
                        
                    status_msg = await send_message(chat_id, "🔍 Анализирую кошелек и генерирую PDF...")
                    
                    # Разрешение .ton
                    if text.endswith(".ton"):
                        try:
                            async with session.get(f"https://tonapi.io/v2/dns/{text}") as r:
                                if r.status == 200:
                                    resolved = (await r.json()).get("item", {}).get("address")
                                    if resolved: text = resolved
                                    else: 
                                        await send_message(chat_id, "❌ Не удалось разрешить .ton домен.")
                                        continue
                        except Exception as e:
                            await send_message(chat_id, f"❌ Ошибка домена: {e}")
                            continue
                    
                    # Анализ и генерация PDF
                    data_result, error = await analyze_ton_wallet_full(text)
                    if error:
                        await send_message(chat_id, f"❌ {error}")
                        continue
                        
                    graph_img = generate_graph(text, data_result["senders"], data_result["receivers"])
                    pdf_file = generate_pdf(data_result, graph_img)
                    
                    await send_document(
                        chat_id, 
                        pdf_file.getvalue(), 
                        f"AML_Report_{text[:6]}.pdf", 
                        f"🛡 AML-Отчет по адресу:\n`{text}`"
                    )
                    
            except Exception as e:
                print(f"Ошибка цикла: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
