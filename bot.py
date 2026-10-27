import os
import sys
import asyncio
import io
import base64
import threading
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta
from decimal import Decimal, getcontext
from xhtml2pdf import pisa
from http.server import BaseHTTPRequestHandler, HTTPServer

import aiohttp
from tonsdk.utils import Address

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ==========================================
# 0. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР (ДЛЯ RENDER)
# ==========================================
def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args): pass
    try:
        server = HTTPServer(('0.0.0.0', port), Handler)
        print(f"✅ Веб-сервер запущен на порту {port}")
        server.serve_forever()
    except Exception as e:
        print(f"❌ Ошибка веб-сервера: {e}")

threading.Thread(target=run_dummy_server, daemon=True).start()

# ==========================================
# 1. КОНФИГУРАЦИЯ
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
# 2. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================
def to_friendly(addr):
    if not addr or addr == "?": return "?"
    try: return Address(addr).to_string(is_user_friendly=True, is_bounceable=False).replace('/', '_').replace('+', '-')
    except: return addr

# ==========================================
# 3. СБОР ДАННЫХ ДЛЯ TON
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

def build_profile(events, names, price, out_pct, peers_n):
    events = sorted(events)
    ins = [e for e in events if e[1] == "IN"]
    outs = [e for e in events if e[1] == "OUT"]
    in_peers = {e[3] for e in ins}; out_peers = {e[3] for e in outs}
    n = len(events)
    sc = {"«Транзитка» (адрес-прокладка)": 0, "Личный кошелёк": 0, "P2P-трейдер / обменник": 0}
    k_tr, k_pe, k_p2p = list(sc)
    if out_pct >= 90: sc[k_tr] += 30
    if peers_n <= 10: sc[k_tr] += 10
    if out_pct < 70: sc[k_pe] += 20
    if peers_n >= 20: sc[k_p2p] += 10
    best = max(sc, key=sc.get)
    conf = "высокая" if sc[best] >= 60 else "средняя" if sc[best] >= 35 else "низкая"
    desc = {
        k_tr: "Адрес-прокладка: почти всё поступившее быстро уходит дальше, на адресе ничего не остаётся.",
        k_pe: "Обычный личный кошелёк: средства в основном остаются на адресе.",
        k_p2p: "Много разных контрагентов: похоже на P2P-трейдера или обменник.",
    }[best]
    amts = [e[2] for e in events]
    typical = statistics.median(amts) * price if amts else 0
    round_pct = sum(1 for a in amts if a == int(a)) / n * 100 if n else 0
    days = {datetime.fromtimestamp(e[0]).date() for e in events if e[0] > 0}
    per_day = n / len(days) if days else 0
    holds = []
    for i in ins:
        nxt = next((o for o in outs if o[0] >= i[0]), None)
        if nxt: holds.append((nxt[0] - i[0]) / 3600)
    hold = f"{statistics.median(holds):.1f} ч" if holds else "—"
    hours = {datetime.fromtimestamp(e[0]).hour for e in events if e[0] > 0}
    night = 0
    for s in range(24):
        c = 0
        while c < 24 and (s + c) % 24 not in hours: c += 1
        night = max(night, c)
    ex_kw = ("binance", "okx", "bybit", "kucoin", "mexc", "bitget", "htx", "exchange")
    ex_ops = sum(1 for e in events if any(k in (names.get(e[3]) or "").lower() for k in ex_kw))
    ex_pct = ex_ops / n * 100 if n else 0
    return {
        "type": best, "conf": conf, "desc": desc,
        "ops": f"{n} ({len(ins)} вх. / {len(outs)} исх.)",
        "peers": f"{len(in_peers | out_peers)} ({len(in_peers)} / {len(out_peers)})",
        "per_day": f"{per_day:.1f}", "hold": hold, "night": f"{night} ч",
        "ex": f"{ex_pct:.0f}%", "round": f"0% / {round_pct:.0f}%", "typical": f"≈ ${typical:.2f}",
    }

# ==========================================
# 4. РИСУЕМ ГРАФ
# ==========================================
def _layout(parents, level2):
    rows, slot = [], 0
    for addr, amt in parents:
        kids = level2.get(addr, [])[:3]
        if kids:
            ky = [-(slot + i) for i in range(len(kids))]
            slot += len(kids)
            py = sum(ky) / len(ky)
        else:
            ky, py = [], -slot
            slot += 1
        rows.append([addr, float(amt), py, list(zip(kids, ky))])
    shift = (slot - 1) / 2
    for r in rows:
        r[2] += shift
        r[3] = [(k, y + shift) for k, y in r[3]]
    return rows, slot

def generate_graph_image(wallet, data):
    names = data["names"]
    ins, n1 = _layout([(a, i["sum"]) for a, i in data["top_senders"]], data.get("in2", {}))
    outs, n2 = _layout([(a, i["sum"]) for a, i in data["top_receivers"]], data.get("out2", {}))
    max_slots = max(n1, n2, 4)
    fig, ax = plt.subplots(figsize=(16, max(10, max_slots * 1.2)), facecolor='white')
    ax.axis("off")
    def arrow(p, q): ax.annotate("", xy=q, xytext=p, arrowprops=dict(arrowstyle="-|>", color="#888", lw=0.6, shrinkA=5, shrinkB=5))
    def label(p, q, amt): ax.text((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, f"{amt:.2f} TON", fontsize=5, ha="center", va="center", color="#222", bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.9))
    def node(x, y, addr, size=50):
        nm = names.get(addr)
        ax.scatter([x], [y], s=size, color=_color(nm), zorder=5)
        short_addr = f"{addr[:6]}...{addr[-4:]}" if len(addr) > 10 else addr
        label_text = f"{short_addr}\n{nm}" if nm else short_addr
        ax.text(x, y - 0.3, label_text, ha="center", va="top", fontsize=4.5, color="#222", zorder=6)
    X_COORDS = [-6, -3, 0, 3, 6]
    for rows, side in ((ins, -1), (outs, 1)):
        for addr, amt, py, kids in rows:
            xp = X_COORDS[1] if side < 0 else X_COORDS[3]
            x2 = X_COORDS[0] if side < 0 else X_COORDS[4]
            p, c = (xp, py), (0, 0)
            s, e = (p, c) if side < 0 else (c, p)
            arrow(s, e); label(s, e, amt); node(xp, py, addr, size=80)
            for (ka, kamt), ky in kids:
                k = (x2, ky)
                s2, e2 = (k, p) if side < 0 else (p, k)
                arrow(s2, e2); label(s2, e2, kamt); node(x2, ky, ka, size=40)
    ax.scatter([0], [0], s=200, color="#007bff", zorder=7)
    ax.text(0, -0.3, f"{wallet[:6]}...{wallet[-4:]}\n{data['dns']}", ha="center", va="top", fontsize=5, weight="bold", zorder=8)
    top = max(n1, n2) / 2 + 1.5
    for x, t in zip(X_COORDS, ["Источники\n2-го шага", "Отправители", "Проверяемый\nадрес", "Получатели", "Получатели\n2-го шага"]):
        ax.text(x, top, t, ha="center", va="bottom", fontsize=7, weight="bold")
    ax.set_xlim(-8, 8); ax.set_ylim(-top - 1, top + 1)
    buf = io.BytesIO(); fig.savefig(buf, format="png", bbox_inches="tight", dpi=250, facecolor='white'); plt.close(fig); buf.seek(0)
    return buf

# ==========================================
# 5. АНАЛИЗ TON (УСКОРЕННЫЙ)
# ==========================================
async def analyze_ton_wallet_max(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            ton_price = 0.0
            async with session.get("https://tonapi.io/v2/rates?tokens=ton&currencies=usd", headers=headers) as r:
                if r.status == 200: ton_price = (await r.json()).get("rates", {}).get("TON", {}).get("prices", {}).get("USD", 0.0)
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200: return None, f"Кошелек не найден."
                data = await r.json()
                balance_dec = Decimal(int(data.get("balance", 0))) / Decimal(10**9)
                status = data.get("status", "unknown")
                interfaces = ", ".join(data.get("interfaces", [])) or "Нет"
            dns_name = "Неидентифицированные"; nft_coll = {}; nft_spam = nft_recv = nft_mint = nft_sent = 0
            try:
                async with session.get(f"{base_url}/accounts/{wallet_address}/nfts?limit=100", headers=headers) as r:
                    if r.status == 200:
                        for item in (await r.json()).get("nft_items", []):
                            name = (item.get("metadata") or {}).get("name") or ""
                            if name.endswith(".ton"): dns_name = name; continue
                            if item.get("trust") == "blacklist": nft_spam += 1
                            cn = (item.get("collection") or {}).get("name")
                            if cn: nft_coll[cn] = nft_coll.get(cn, 0) + 1
                me = to_friendly(wallet_address)
                async with session.get(f"{base_url}/accounts/{wallet_address}/nfts/history?limit=100", headers=headers) as r:
                    if r.status == 200:
                        for ev in (await r.json()).get("events", []):
                            for act in ev.get("actions", []):
                                t = act.get("NftItemTransfer")
                                if not t: continue
                                snd = (t.get("sender") or {}).get("address"); rcp = (t.get("recipient") or {}).get("address")
                                if rcp and to_friendly(rcp) == me:
                                    if snd: nft_recv += 1
                                    else: nft_mint += 1
                                elif snd and to_friendly(snd) == me: nft_sent += 1
            except Exception: pass
            events = []; total_in = Decimal(0); total_out = Decimal(0); unique_peers = set(); total_txs = 0; timestamps = []
            senders_dict, receivers_dict = {}, {}; round_sums = 0
            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=100", headers=headers) as r:
                if r.status == 200:
                    for tx in (await r.json()).get("transactions", []):
                        utime = tx.get("utime", 0)
                        if utime > 0: timestamps.append(utime)
                        in_msg = tx.get("in_msg")
                        if in_msg and in_msg.get("value"):
                            amt = Decimal(int(in_msg.get("value", 0))) / Decimal(10**9)
                            if amt >= Decimal('0.1'): 
                                total_in += amt; total_txs += 1
                                if amt % 1 == 0: round_sums += 1
                                src = to_friendly(in_msg.get("source", {}).get("address", "?"))
                                if src and src != "?":
                                    unique_peers.add(src); events.append((utime, "IN", float(amt), src))
                                    if src not in senders_dict: senders_dict[src] = {'count': 0, 'sum': Decimal(0), 'last': datetime.fromtimestamp(utime).strftime("%d.%m.%Y")}
                                    senders_dict[src]['count'] += 1; senders_dict[src]['sum'] += amt; senders_dict[src]['last'] = datetime.fromtimestamp(utime).strftime("%d.%m.%Y")
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amt = Decimal(int(out.get("value", 0))) / Decimal(10**9)
                                if amt >= Decimal('0.1'):
                                    total_out += amt; total_txs += 1
                                    if amt % 1 == 0: round_sums += 1
                                    dest = to_friendly(out.get("destination", {}).get("address", "?"))
                                    if dest and dest != "?":
                                        unique_peers.add(dest); events.append((utime, "OUT", float(amt), dest))
                                        if dest not in receivers_dict: receivers_dict[dest] = {'count': 0, 'sum': Decimal(0), 'last': datetime.fromtimestamp(utime).strftime("%d.%m.%Y")}
                                        receivers_dict[dest]['count'] += 1; receivers_dict[dest]['sum'] += amt; receivers_dict[dest]['last'] = datetime.fromtimestamp(utime).strftime("%d.%m.%Y")
            top_senders = sorted(senders_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            top_receivers = sorted(receivers_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            
            in2, out2 = {}, {}
            tasks = []
            for a, _ in top_senders[:3]: tasks.append(fetch_counterparties(session, headers, a))
            for a, _ in top_receivers[:3]: tasks.append(fetch_counterparties(session, headers, a))
            
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for i, res in enumerate(results):
                    if isinstance(res, Exception): continue
                    if i < len(top_senders[:3]):
                        addr = top_senders[i][0]
                        in2[addr] = res[0]
                    else:
                        addr = top_receivers[i - len(top_senders[:3])][0]
                        out2[addr] = res[1]

            all_addrs = {a for a, _ in top_senders} | {a for a, _ in top_receivers}
            for lst in list(in2.values()) + list(out2.values()): all_addrs |= {a for a, _ in lst}
            names = await fetch_names(session, headers, all_addrs)
            usd_in = float(total_in) * ton_price; usd_out = float(total_out) * ton_price
            first_date = datetime.fromtimestamp(min(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            last_date = datetime.fromtimestamp(max(timestamps)).strftime("%d.%m.%Y %H:%M") if timestamps else "Нет"
            check_time = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M:%S")
            out_pct = (float(total_out) / float(total_in) * 100) if total_in > 0 else 0
            profile = build_profile(events, names, ton_price, out_pct, len(unique_peers))
            return {
                "wallet": wallet_address, "dns": dns_name, "status": status, "interfaces": interfaces,
                "balance": f"{balance_dec.normalize():f}", "balance_usd": f"${float(balance_dec) * ton_price:.4f}",
                "usd_in": f"${usd_in:.2f}", "usd_out": f"${usd_out:.2f}", "ton_price": ton_price,
                "total_in": f"{total_in.normalize():f}", "total_out": f"{total_out.normalize():f}",
                "difference": f"{(total_in - total_out).normalize():f}",
                "peers_count": len(unique_peers), "first_tx": first_date, "last_tx": last_date, "check_time": check_time,
                "ops_in": len([e for e in events if e[1] == "IN"]), "ops_out": len([e for e in events if e[1] == "OUT"]),
                "nft": {"recv": nft_recv, "mint": nft_mint, "sent": nft_sent, "spam": nft_spam, "coll": nft_coll},
                "profile": profile, "top_senders": top_senders, "top_receivers": top_receivers,
                "in2": in2, "out2": out2, "names": names
            }, None
    except Exception as e: return None, f"Ошибка API: {e}"

# ==========================================
# 6. ГЕНЕРАЦИЯ PDF
# ==========================================
def generate_aml_pdf(data: dict, graph_img):
    graph_html = f'<div style="text-align:center; margin-bottom:10px;"><img src="data:image/png;base64,{base64.b64encode(graph_img.getvalue()).decode("utf-8")}" width="700" /></div>' if graph_img else ""
    senders_html = ""; receivers_html = ""
    if data['top_senders']:
        senders_html += f'<div style="background-color:#d9d9d9; padding:5px; font-weight:bold; font-size:10pt; margin-top:15px; margin-bottom:5px;">Отправители</div><table class="peers-table"><thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th><th>Период</th></tr></thead><tbody>'
        for addr, info in data['top_senders']:
            label = data['names'].get(addr) or "—"; usd_sum = float(info['sum']) * data['ton_price']; clean_addr = addr.replace('/', '_')
            senders_html += f"<tr><td style='word-break: break-all; width: 40%;'>{clean_addr}</td><td>{label}</td><td>{info['count']}</td><td><b>{info['sum'].normalize():f} TON</b><br><span style='color:#777;'>≈ ${usd_sum:.2f}</span></td><td>{info['last']}</td></tr>"
        senders_html += "</tbody></table>"
    if data['top_receivers']:
        receivers_html += f'<div style="background-color:#d9d9d9; padding:5px; font-weight:bold; font-size:10pt; margin-top:15px; margin-bottom:5px;">Получатели</div><table class="peers-table"><thead><tr><th>Адрес</th><th>Метка</th><th>Оп.</th><th>Сумма</th><th>Период</th></tr></thead><tbody>'
        for addr, info in data['top_receivers']:
            label = data['names'].get(addr) or "—"; usd_sum = float(info['sum']) * data['ton_price']; clean_addr = addr.replace('/', '_')
            receivers_html += f"<tr><td style='word-break: break-all; width: 40%;'>{clean_addr}</td><td>{label}</td><td>{info['count']}</td><td><b>{info['sum'].normalize():f} TON</b><br><span style='color:#777;'>≈ ${usd_sum:.2f}</span></td><td>{info['last']}</td></tr>"
        receivers_html += "</tbody></table>"
    nf = data["nft"]; nft_total = nf["recv"] + nf["mint"] + nf["sent"]; coll = ", ".join(f"{k} ({v})" for k, v in nf["coll"].items()) or "нет"
    p = data["profile"]; diff = data["difference"]; diff = diff if diff.startswith("-") else "+" + diff
    html = f"""<html><head><style>
        @font-face {{ font-family: 'DejaVu'; src: url('{FONT_REG}'); }}
        @font-face {{ font-family: 'DejaVu'; src: url('{FONT_BOLD}'); font-weight: bold; }}
        @page {{ size: A4; margin: 1cm; }} body {{ font-family: 'DejaVu'; color: #333; font-size: 9pt; }}
        .header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 20px; }}
        .header-table td {{ border: 1px solid #ddd; padding: 6px; vertical-align: top; }}
        .header-table td.label {{ background-color: #f8f9fa; font-weight: bold; width: 35%; color: #555; }}
        .section-title {{ color: #1a4fa0; font-weight: bold; font-size: 14pt; margin-top: 18px; margin-bottom: 6px; }}
        .small {{ font-size: 8pt; color: #555; }}
        .peers-table {{ width: 100%; border-collapse: collapse; margin-bottom: 15px; }}
        .peers-table th {{ background-color: #f1f1f1; padding: 6px; text-align: left; border-bottom: 2px solid #ccc; font-size: 8pt; color: #333; }}
        .peers-table td {{ padding: 6px; border-bottom: 1px solid #eee; font-size: 8pt; vertical-align: top; }}
        .legend {{ font-size: 8pt; color: #555; margin-top: 5px; text-align: left; }}
    </style></head><body>
        <h1 style="text-align:center; color:#2c3e50; font-size: 16pt;">AML-отчет по криптоадресу</h1>
        <table class="header-table">
            <tr><td class="label">Дата и время проверки</td><td>{data['check_time']} (Europe/Minsk)</td></tr>
            <tr><td class="label">Адрес кошелька</td><td>{data['wallet']}</td></tr>
            <tr><td class="label">Сеть</td><td>TON</td></tr>
            <tr><td class="label">Метка адреса</td><td>{data['dns']}</td></tr>
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
        <div class="legend">● синий — проверяемый адрес; ● зеленый — биржа; ● оранжевый — миксер/скам; ● желтый — DEX/обменник; ● серый — неидентифицированный.</div>
        {senders_html if senders_html else ""}
        {receivers_html if receivers_html else ""}
        <div class="section-title">3. NFT-операции</div>
        <p>Получено NFT: <b>{nf['recv']}</b>, выпущено на адрес: <b>{nf['mint']}</b>, отправлено: <b>{nf['sent']}</b>, спам-NFT: <b>{nf['spam']}</b> (по последним {nft_total} переводам NFT).</p>
        <p class="small">Коллекции: {coll}</p>
        <div class="section-title">4. Профиль владельца адреса</div>
        <p>Вероятный тип владельца: <b>{p['type']}</b> · уверенность <b>{p['conf']}</b>.</p>
        <p class="small">{p['desc']}<br/>Метка адреса: {data['dns']}</p>
        <table class="peers-table">
          <tr><td>Операций в анализе</td><td><b>{p['ops']}</b></td><td>Стейблкоины / «круглые» суммы</td><td><b>{p['round']}</b></td></tr>
          <tr><td>Разных контрагентов</td><td><b>{p['peers']}</b></td><td>Типичная сумма</td><td><b>{p['typical']}</b></td></tr>
          <tr><td>Операций в активный день</td><td><b>{p['per_day']}</b></td><td>Сколько держит средства (медиана)</td><td><b>{p['hold']}</b></td></tr>
          <tr><td>Ночная пауза</td><td><b>{p['night']}</b></td><td>Доля операций с биржами</td><td><b>{p['ex']}</b></td></tr>
        </table>
        <p class="small">Профиль построен по последним операциям и является вероятностной оценкой поведения, а не установлением личности владельца.</p>
        <div class="section-title">5. Вывод и примечания</div>
        <table class="peers-table">
          <thead><tr><th>Актив</th><th>Поступило</th><th>Отправлено</th><th>Разница</th><th>≈ USD</th></tr></thead>
          <tbody>
            <tr><td><b>TON</b></td><td>{data['total_in']}</td><td>{data['total_out']}</td><td>{diff}</td><td>{data['usd_in']} / {data['usd_out']}</td></tr>
            <tr><td><b>Операций</b></td><td><b>{data['ops_in']}</b></td><td><b>{data['ops_out']}</b></td><td></td><td></td></tr>
          </tbody>
        </table>
    </body></html>"""
    pdf_buffer = io.BytesIO(); pisa.CreatePDF(html.encode('utf-8'), dest=pdf_buffer, encoding='utf-8'); pdf_buffer.seek(0)
    return pdf_buffer

# ==========================================
# 7. ОТПРАВКА ЧЕРЕЗ HTTP API
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
# 8. ОСНОВНОЙ ЦИКЛ
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
                    
                    if text == "/start":
                        await send_message(chat_id, "👋 Отправь мне TON-адрес (UQ... или .ton), и я пришлю PDF-отчет.")
                        continue
                        
                    if not text.startswith(("UQ", "EQ", "0:")) and not text.endswith(".ton"):
                        await send_message(chat_id, "⚠️ Пожалуйста, отправьте TON-адрес (начинается на UQ/EQ/0:) или .ton домен.")
                        continue
                        
                    await send_message(chat_id, "🔍 Анализирую кошелек и генерирую PDF...")
                    
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
                    
                    data_result, error = await analyze_ton_wallet_max(text)
                    if error:
                        await send_message(chat_id, f"❌ {error}")
                        continue
                        
                    graph_img = generate_graph_image(text, data_result)
                    pdf_file = generate_aml_pdf(data_result, graph_img)
                    
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
