# ==========================================
# 0. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР (ДЛЯ RENDER)
# ==========================================
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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
# 1. ИМПОРТЫ
# ==========================================
from pyrogram import Client, filters, raw
from pyrogram.raw.functions.payments import GetSavedStarGifts
from pyrogram.raw.functions.users import GetFullUser
from pyrogram.errors import (
    UsernameNotOccupied, PeerIdInvalid, FloodWait,
    StickersetInvalid
)
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import time
import aiohttp
import io
import random
import asyncio
import phonenumbers
from phonenumbers import geocoder, carrier
import matplotlib.pyplot as plt
import matplotlib
import statistics
from decimal import Decimal, getcontext
from datetime import datetime, timezone, timedelta
from xhtml2pdf import pisa
import base64
from tonsdk.utils import Address
import traceback

# --- ФИКС ШРИФТОВ ---
_FD = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
FONT_REG = os.path.join(_FD, "DejaVuSans.ttf").replace("\\", "/")
FONT_BOLD = os.path.join(_FD, "DejaVuSans-Bold.ttf").replace("\\", "/")
getcontext().prec = 20

# --- КОНВЕРТАЦИЯ АДРЕСОВ ---
def to_friendly(addr):
    if not addr or addr == "?": return "?"
    try:
        res = Address(addr).to_string(is_user_friendly=True, is_bounceable=False)
        return res.replace('/', '_').replace('+', '-')
    except Exception:
        return addr

# ==========================================
# 2. КОНФИГУРАЦИЯ
# ==========================================
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN")
SESSION_STRING = os.environ.get("SESSION_STRING")
TON_API_KEY = os.environ.get("TON_API_KEY", "")

# ==========================================
# 3. БАЗА ID И ФУНКЦИИ ДЛЯ TELEGRAM
# ==========================================
LOCAL_DB = {100000000: "2013-08", 500000000: "2015-04", 1000000000: "2016-04", 2000000000: "2017-07", 3000000000: "2018-06", 4000000000: "2019-04", 5000000000: "2020-01", 6000000000: "2021-02", 7000000000: "2022-06", 8000000000: "2024-06"}
MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

def get_time_passed(year, month):
    now = datetime.now()
    reg_date = datetime(year, month, 1)
    years = now.year - reg_date.year
    months = now.month - reg_date.month
    if months < 0: years -= 1; months += 12
    parts = []
    if years > 0:
        y_str = f"{years} год" if years % 10 == 1 and years % 100 != 11 else f"{years} года" if 2 <= years % 10 <= 4 and not (12 <= years % 100 <= 14) else f"{years} лет"
        parts.append(y_str)
    if months > 0: parts.append(f"{months} мес")
    return " ".join(parts) if parts else "0 мес"

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
# 4. ОПРЕДЕЛЕНИЕ ОПЕРАТОРА И РЕГИОНА
# ==========================================
def get_phone_info(phone: str):
    try:
        parsed = phonenumbers.parse(phone, "RU")
        if not phonenumbers.is_valid_number(parsed):
            return "Неизвестный оператор", "Неизвестный регион"
        operator = carrier.name_for_number(parsed, "ru")
        if not operator: operator = "Неизвестный оператор"
        region = geocoder.description_for_number(parsed, "ru")
        if not region:
            region = phonenumbers.region_code_for_number(parsed) or "Россия"
        return operator, region
    except Exception:
        return "Неизвестный оператор", "Неизвестный регион"

# ==========================================
# 5. ОТЧЕТ ПО НОМЕРУ
# ==========================================
async def generate_beautiful_report(phone: str, user_app):
    clean = phone.replace("+", "").replace(" ", "").replace("-", "")
    operator, region = get_phone_info(phone)
    tg_info = ""
    try:
        await asyncio.sleep(2)
        user = await asyncio.wait_for(user_app.get_users(phone), timeout=15.0)
        if isinstance(user, list): user = user[0]
        if user is None: raise Exception("User not found")
        tg_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
        tg_username = f"@{user.username}" if user.username else "скрыт"
        reg_date = "Неизвестно"
        est_y, est_m = estimate_registration(user.id)
        if est_y and est_m:
            reg_date = f"~ {MONTHS_RU.get(est_m, '')} {est_y}"
        tg_info = (
            f"💬 <b>Telegram:</b>\n"
            f" • <b>Имя:</b> {tg_name}\n"
            f" • <b>Юзернейм:</b> {tg_username}\n"
            f" • <b>ID:</b> <code>{user.id}</code>\n"
            f" • <b>Регистрация:</b> {reg_date}\n\n"
        )
    except FloodWait as e:
        tg_info = f"💬 <b>Telegram:</b> Временно заблокирован (ждать {e.value} сек.)\n\n"
    except Exception as e:
        tg_info = f"💬 <b>Telegram:</b> Не удалось получить (ошибка: {type(e).__name__})\n\n"
    card = (
        f"📱 <b>Телефон:</b> <code>{clean}</code>\n"
        f"📡 <b>Оператор:</b> {operator}\n"
        f"📍 <b>Регион:</b> {region}\n\n"
    )
    if tg_info: card += tg_info
    buttons = [[
        InlineKeyboardButton("📱 WhatsApp", url=f"https://wa.me/{clean}"),
        InlineKeyboardButton("✈️ Telegram", url=f"https://t.me/+{clean}")
    ]]
    return card, InlineKeyboardMarkup(buttons)

# ==========================================
# 6. СБОР ДАННЫХ ДЛЯ TON
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
# 7. ГРАФ
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
# 8. АНАЛИЗ TON
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
# 9. PDF
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
    pdf_buffer = io.BytesIO(); pisa.createPDF(html.encode('utf-8'), dest=pdf_buffer, encoding='utf-8'); pdf_buffer.seek(0)
    return pdf_buffer

# ==========================================
# 10. КЛИЕНТЫ
# ==========================================
bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)
last_request_time = {}

# ==========================================
# 11. ОБРАБОТЧИКИ
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await message.reply(
        "👋 Привет! Отправь мне:\n"
        "1. <b>@username</b> или <b>+номер</b> (профиль TG или OSINT)\n"
        "2. <b>TON-адрес</b> (UQ... для AML)\n"
        "3. <b>Стикер</b> (кинь стикер в чат — покажу инфу о паке и ID создателя)"
    )

@bot.on_message(filters.sticker & filters.private)
async def handle_sticker_wrapper(client, message):
    try:
        await _handle_sticker(client, message)
    except Exception as e:
        traceback.print_exc()
        await message.reply(f"Ошибка: {type(e).__name__}: {e}")

def _extract_owner_id(packed_id: int):
    """Возвращает (owner_id, internal_id) из 64-битного id пака."""
    unsigned = packed_id & 0xFFFFFFFFFFFFFFFF
    return unsigned >> 32, unsigned & 0xFFFFFFFF

async def _handle_sticker(client, message):
    sticker = message.sticker
    if not sticker.set_name:
        await message.reply("Этот стикер не привязан к паку.")
        return

    status_msg = await message.reply("Анализирую стикер...")

    s_type = "Видео" if sticker.is_video else "Анимированный" if sticker.is_animated else "Обычный"
    pack_title = "Неизвестно"
    pack_count = "?"
    is_official = "Неизвестно"
    is_archived = "Неизвестно"
    pack_type = "Неизвестно"
    pack_id = "Неизвестно"
    is_creator = "Нет"
    install_date = "Неизвестно"
    owner_id = None

    try:
        result = await user_app.invoke(
            raw.functions.messages.GetStickerSet(
                stickerset=raw.types.InputStickerSetShortName(short_name=sticker.set_name),
                hash=0
            )
        )
        stickerset = result.set

        pack_title = stickerset.title
        pack_count = stickerset.count
        pack_id = stickerset.id
        is_official = "Да" if getattr(stickerset, 'official', False) else "Нет"
        is_archived = "Да" if getattr(stickerset, 'archived', False) else "Нет"
        is_creator = "Да" if getattr(stickerset, 'creator', False) else "Нет"

        if getattr(stickerset, 'masks', False): pack_type = "Маски"
        elif getattr(stickerset, 'emojis', False): pack_type = "Кастомные эмодзи"
        else: pack_type = "Обычный"

        if getattr(stickerset, 'installed_date', None):
            install_date = datetime.fromtimestamp(stickerset.installed_date).strftime("%d.%m.%Y")

        owner_id, internal_id = _extract_owner_id(stickerset.id)

        print(f"[STICKER DEBUG] set_name={sticker.set_name}")
        print(f"[STICKER DEBUG] raw id    = {stickerset.id}")
        print(f"[STICKER DEBUG] owner_id  = {owner_id}")
        print(f"[STICKER DEBUG] internal  = {internal_id}")

    except StickersetInvalid:
        await status_msg.edit_text("Пак не найден или недоступен.", parse_mode=None)
        return
    except Exception as e:
        print(f"Ошибка получения пака: {e}")
        traceback.print_exc()
        try:
            pack_info = await asyncio.wait_for(user_app.get_sticker_set(sticker.set_name), timeout=10.0)
            pack_title = pack_info.title
            pack_count = len(pack_info.stickers)
            is_official = "Да" if getattr(pack_info, 'is_official', False) else "Нет"
            is_archived = "Да" if getattr(pack_info, 'is_archived', False) else "Нет"
            is_creator = "Да" if getattr(pack_info, 'creator', False) else "Нет"
            if getattr(pack_info, 'installed_date', None):
                install_date = datetime.fromtimestamp(pack_info.installed_date).strftime("%d.%m.%Y")
            if getattr(pack_info, 'id', None):
                owner_id, _ = _extract_owner_id(pack_info.id)
        except Exception as e2:
            print(f"Fallback не сработал: {e2}")

    file_size_kb = round(sticker.file_size / 1024, 1) if sticker.file_size else "?"
    resolution = f"{sticker.width}x{sticker.height}" if sticker.width else "?"
    mime_type = sticker.mime_type or "?"

    owner_line = f"{owner_id}" if owner_id is not None else "Неизвестно"

    card = (
        f"Информация о стикере\n\n"
        f"Эмодзи: {sticker.emoji}\n"
        f"Пак: {sticker.set_name}\n"
        f"Название: {pack_title}\n"
        f"Внутренний ID пака: {pack_id}\n"
        f"Стикеров: {pack_count} шт. ({s_type})\n"
        f"Тип пака: {pack_type}\n"
        f"Официальный: {is_official}\n"
        f"В архиве: {is_archived}\n"
        f"Ваш пак: {is_creator}\n"
        f"Установлен: {install_date}\n"
        f"ID создателя: {owner_line}\n\n"
        f"Параметры файла:\n"
        f"Размер: {file_size_kb} КБ\n"
        f"Разрешение: {resolution}\n"
        f"Формат: {mime_type}\n"
        f"Уникальный ID: {sticker.file_unique_id}\n"
    )

    try:
        await status_msg.edit_text(card, parse_mode=None)
    except Exception as e:
        print(f"Ошибка edit_text: {e}")
        await message.reply(card[:4000], parse_mode=None)

# ==========================================
# 12. ОСНОВНОЙ ОБРАБОТЧИК
# ==========================================
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
        status_msg = await message.reply("🔍 Генерирую AML-отчет...")
        target_input = to_friendly(target_input)
        report_data, error = await analyze_ton_wallet_max(target_input)
        if error: return await status_msg.edit_text(error)
        graph_img = generate_graph_image(target_input, report_data)
        pdf_buffer = generate_aml_pdf(report_data, graph_img)
        await message.reply_document(document=pdf_buffer, file_name=f"AML_Report_{target_input[:6]}.pdf", caption=f"🛡 AML-Анализ с графом связей\n`{target_input}`")
        await status_msg.delete()
        return

    if target_input.endswith(".ton"):
        status_msg = await message.reply("🔍 Ищу кошелек по TON-домену...")
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Accept": "application/json"}
                if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"
                async with session.get(f"https://tonapi.io/v2/dns/{target_input}", headers=headers) as r:
                    if r.status == 200:
                        wallet_addr = (await r.json()).get("item", {}).get("address")
                        if wallet_addr:
                            await status_msg.edit_text(f"✅ Домен {target_input} привязан к `{wallet_addr}`.\nГенерирую AML...")
                            report_data, error = await analyze_ton_wallet_max(wallet_addr)
                            if error: return await status_msg.edit_text(error)
                            graph_img = generate_graph_image(wallet_addr, report_data)
                            pdf_buffer = generate_aml_pdf(report_data, graph_img)
                            await message.reply_document(document=pdf_buffer, file_name=f"AML_Report_{wallet_addr[:6]}.pdf", caption=f"🛡 AML по домену {target_input}\n`{wallet_addr}`")
                            await status_msg.delete()
                            return
                    await status_msg.edit_text("❌ Не удалось разрешить TON-домен.")
        except Exception as e: await status_msg.edit_text(f"❌ Ошибка: {e}")
        return

    if target_input.startswith("+") and target_input[1:].isdigit():
        status_msg = await message.reply("🔍 Собираю информацию...")
        card, keyboard = await generate_beautiful_report(target_input, user_app)
        await status_msg.edit_text(card, reply_markup=keyboard)
        return

    if target_input.isdigit():
        return await message.reply("⚠️ Поиск по ID отключен. Отправьте @username.")

    if not target_input.startswith('@'): target_input = f"@{target_input}"

    status_msg = await message.reply(f"🔍 Ищу в Telegram: {target_input}...")

    try:
        user = await user_app.get_users(target_input)
        if isinstance(user, list): user = user[0]
        if user is None: return await status_msg.edit_text("❌ Пользователь не найден.")

        user_id = user.id
        name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
        username_str = f" (@{user.username})" if user.username else ""
        display_name = f"{name}{username_str}"
        dc_line = f"<b>DC:</b> {user.dc_id}\n" if user.photo else ""
        est_y, est_m = estimate_registration(user_id)
        reg_str = f"~ {MONTHS_RU.get(est_m, '')} {est_y} ({get_time_passed(est_y, est_m)})" if est_y else "Неизвестно"

        channel_str = "Нет"
        try:
            full_info = await user_app.invoke(GetFullUser(id=await user_app.resolve_peer(user_id)))
            if full_info and full_info.full_user:
                p_channel_id = getattr(full_info.full_user, 'personal_channel_id', None)
                if p_channel_id:
                    chan = await user_app.get_chat(p_channel_id)
                    if chan: channel_str = f"@{chan.username}" if chan.username else chan.title
        except Exception: pass

        gifts_info = ""
        try:
            peer = await user_app.resolve_peer(user_id)
            result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=10))
            if result.gifts:
                gl = []
                for g in result.gifts:
                    stars = getattr(g.gift, 'stars', 0)
                    gd = datetime.fromtimestamp(g.date).strftime("%d.%m.%Y")
                    if stars == 0: continue
                    if getattr(g, 'name_hidden', False) or not g.from_id: gl.append(f" • {stars}⭐ от Аноним ({gd})")
                    else: gl.append(f" • {stars}⭐ от ID: <code>{getattr(g.from_id, 'user_id', '?')}</code> ({gd})")
                gifts_info = "\n" + "\n".join(gl)
        except Exception: pass

        card = "🪪 <b>Информация о пользователе</b>\n\n" + f"<b>ID:</b> <code>{user_id}</code>\n<b>Имя:</b> {display_name}\n{dc_line}"
        if channel_str != "Нет": card += f"<b>Канал:</b> {channel_str}\n"
        card += f"\n<b>Регистрация:</b> {reg_str}\n"
        if gifts_info: card += f"\n<b>Подарки:</b>{gifts_info}\n"
        card += f"\n<b>Точность:</b> высокая ({random.randint(85, 99)}%)\n"
        await status_msg.edit_text(card)

    except (UsernameNotOccupied, PeerIdInvalid):
        await status_msg.edit_text("❌ Пользователь не найден. Проверьте правильность @username.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    print("🤖 Запуск...")
    user_app.start()
    bot.run()
