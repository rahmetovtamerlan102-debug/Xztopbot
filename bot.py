import os
import threading
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from pyrogram import Client, filters
from pyrogram.errors import UsernameNotOccupied, PeerIdInvalid, FloodWait
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

import aiohttp
import io
import base64
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime
from decimal import Decimal, getcontext
import statistics
import phonenumbers
from phonenumbers import geocoder, carrier

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
TON_API_KEY = os.environ.get("TON_API_KEY", "")
getcontext().prec = 20

bot = Client("my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, in_memory=True)
user_app = Client("my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, in_memory=True, no_updates=True)

app = FastAPI()
class SearchRequest(BaseModel): query: str

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ TON
# ==========================================
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
    
    return {
        "type": best, "conf": conf, "desc": desc,
        "ops": f"{n} ({len(ins)} вх. / {len(outs)} исх.)",
        "peers": str(peers_n),
        "per_day": f"{per_day:.1f}", "hold": hold,
        "round": f"0% / {round_pct:.0f}%", "typical": f"≈ ${typical:.2f}",
    }

# ==========================================
# ИСПРАВЛЕННАЯ ГЕНЕРАЦИЯ ГРАФА
# ==========================================
def generate_ton_graph(wallet, senders, receivers):
    G = nx.DiGraph()
    sw = wallet[:6] + "..." + wallet[-4:]
    G.add_node(sw, color='#007aff', size=2000)
    
    # senders и receivers здесь — это списки кортежей (addr, Decimal_sum)
    for item in senders[:5]: 
        a = str(item[0])
        amt = float(item[1])
        G.add_edge(a[:6]+"..."+a[-4:], sw, label=f"{amt:.2f} TON", color='#4dcd5e')
        
    for item in receivers[:5]: 
        a = str(item[0])
        amt = float(item[1])
        G.add_edge(sw, a[:6]+"..."+a[-4:], label=f"{amt:.2f} TON", color='#e85d5d')
        
    if len(G.nodes) <= 1: return None
    
    plt.figure(figsize=(10, 8))
    pos = nx.spring_layout(G, k=1, seed=42)
    nx.draw_networkx_nodes(G, pos, node_color=[G.nodes[n].get('color', '#888') for n in G.nodes], node_size=1500)
    nx.draw_networkx_edges(G, pos, edge_color=[G.edges[e].get('color', '#888') for e in G.edges], width=2, arrowsize=20)
    nx.draw_networkx_labels(G, pos, font_size=8, font_weight='bold')
    nx.draw_networkx_edge_labels(G, pos, edge_labels=nx.get_edge_attributes(G, 'label'), font_size=7)
    plt.axis('off')
    buf = io.BytesIO(); plt.savefig(buf, format='png', bbox_inches='tight', dpi=100); plt.close()
    return base64.b64encode(buf.read()).decode('utf-8')

# ==========================================
# АНАЛИЗ TON
# ==========================================
async def analyze_ton_wallet_full(wallet_address: str):
    base_url = "https://tonapi.io/v2"
    headers = {"Accept": "application/json"}
    if TON_API_KEY: headers["Authorization"] = f"Bearer {TON_API_KEY}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/accounts/{wallet_address}", headers=headers) as r:
                if r.status != 200: return None, "Кошелек не найден"
                acc = await r.json()
                balance = Decimal(int(acc.get("balance", 0))) / Decimal(10**9)
                status = acc.get("status", "unknown")
                
            dns_name = "Неидентифицированные"
            async with session.get(f"{base_url}/accounts/{wallet_address}/nfts?limit=100", headers=headers) as r:
                if r.status == 200:
                    for item in (await r.json()).get("nft_items", []):
                        name = (item.get("metadata") or {}).get("name") or ""
                        if name.endswith(".ton"): dns_name = name

            events = []
            total_in, total_out = Decimal(0), Decimal(0)
            senders_dict, receivers_dict = {}, {}
            
            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=100", headers=headers) as r:
                if r.status == 200:
                    for tx in (await r.json()).get("transactions", []):
                        utime = tx.get("utime", 0)
                        if tx.get("in_msg") and tx["in_msg"].get("value"):
                            amt = Decimal(int(tx["in_msg"]["value"])) / Decimal(10**9)
                            if amt >= Decimal('0.1'):
                                total_in += amt
                                src = tx["in_msg"].get("source", {}).get("address", "?")
                                if src != "?":
                                    events.append((utime, "IN", float(amt), src))
                                    if src not in senders_dict: senders_dict[src] = {'sum': Decimal(0), 'last': utime}
                                    senders_dict[src]['sum'] += amt; senders_dict[src]['last'] = utime
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amt = Decimal(int(out["value"])) / Decimal(10**9)
                                if amt >= Decimal('0.1'):
                                    total_out += amt
                                    dst = out.get("destination", {}).get("address", "?")
                                    if dst != "?":
                                        events.append((utime, "OUT", float(amt), dst))
                                        if dst not in receivers_dict: receivers_dict[dst] = {'sum': Decimal(0), 'last': utime}
                                        receivers_dict[dst]['sum'] += amt; receivers_dict[dst]['last'] = utime
            
            # Для API-ответа (со словарями)
            top_senders_list = sorted(senders_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            top_receivers_list = sorted(receivers_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            
            senders_api = [{"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—"} for a, d in top_senders_list]
            receivers_api = [{"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—"} for a, d in top_receivers_list]

            # Для графа (только кортежи адрес-сумма)
            top_senders_graph = [(a, d['sum']) for a, d in top_senders_list]
            top_receivers_graph = [(a, d['sum']) for a, d in top_receivers_list]

            nft_stats = {"recv": 0, "mint": 0, "sent": 0, "spam": 0, "collections": {}}
            async with session.get(f"{base_url}/accounts/{wallet_address}/nfts?limit=100", headers=headers) as r:
                if r.status == 200:
                    for item in (await r.json()).get("nft_items", []):
                        if item.get("trust") == "blacklist": nft_stats["spam"] += 1
                        else:
                            coll = (item.get("collection") or {}).get("name")
                            if coll: nft_stats["collections"][coll] = nft_stats["collections"].get(coll, 0) + 1

            out_pct = (float(total_out) / float(total_in) * 100) if total_in > 0 else 0
            profile = build_profile(events, {}, 1.0, out_pct, len(senders_dict) + len(receivers_dict))
            
            # Генерация графа с чистыми кортежами
            graph_b64 = generate_ton_graph(wallet_address, top_senders_graph, top_receivers_graph)

            return {
                "balance": f"{balance.normalize():f}",
                "status": status,
                "dns": dns_name,
                "total_in": f"{total_in.normalize():f}",
                "total_out": f"{total_out.normalize():f}",
                "senders": senders_api,
                "receivers": receivers_api,
                "nft": nft_stats,
                "profile": profile,
                "graph": graph_b64
            }, None
    except Exception as e:
        return None, str(e)

# ==========================================
# ФУНКЦИЯ ПОЛУЧЕНИЯ ПОДАРКОВ
# ==========================================
async def get_profile_gifts(user_id: int):
    try:
        from pyrogram.raw.functions.payments import GetSavedStarGifts
        peer = await user_app.resolve_peer(user_id)
        result = await user_app.invoke(GetSavedStarGifts(peer=peer, offset="", limit=20, exclude_unsaved=True))
        if not result or not result.gifts: return "Нет"
        
        gift_list = []
        for g in result.gifts:
            stars = getattr(g.gift, 'stars', 0)
            if stars == 0: continue
            sender = "Аноним"
            if getattr(g, 'from_id', None):
                sender = str(g.from_id.user_id) if hasattr(g.from_id, 'user_id') else str(g.from_id)
            gift_list.append({"sender": sender, "stars": stars})
            
        return gift_list if gift_list else "Нет"
    except Exception as e:
        print(f"[GIFT ERROR] {e}")
        return "Ошибка загрузки"

# ==========================================
# API ОБРАБОТЧИК
# ==========================================
@app.post("/api/search")
async def handle_search(req: SearchRequest):
    query = req.query.strip()
    
    if query.startswith(("UQ", "EQ", "0:")) or query.endswith(".ton"):
        data, error = await analyze_ton_wallet_full(query)
        if error: return JSONResponse({"error": error})
        return JSONResponse({
            "type": "TON Кошелек",
            "result": {
                "Баланс": f"{data['balance']} TON",
                "Метка": data['dns'],
                "Статус": data['status'],
                "Всего вход": f"{data['total_in']} TON",
                "Всего выход": f"{data['total_out']} TON"
            },
            "senders": data["senders"],
            "receivers": data["receivers"],
            "nft": data["nft"],
            "profile": data["profile"],
            "graph": data["graph"]
        })

    elif query.startswith("+") and query[1:].isdigit():
        try:
            parsed = phonenumbers.parse(query, "RU")
            operator = carrier.name_for_number(parsed, "ru") or "Неизвестно"
            region = geocoder.description_for_number(parsed, "ru") or "Неизвестно"
            result_data = {"Номер": query.replace("+", ""), "Оператор": operator, "Регион": region, "Telegram": "Не привязан"}
            try:
                user = await user_app.get_users(query)
                if user:
                    result_data["Telegram"] = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
                    result_data["Username"] = f"@{user.username}" if user.username else "Нет"
                    result_data["ID"] = str(user.id)
                    result_data["Подарки"] = await get_profile_gifts(user.id)
            except: pass
            return JSONResponse({"type": "Телефон", "result": result_data})
        except Exception as e: return JSONResponse({"error": f"Ошибка: {str(e)}"})

    elif query.startswith("@") or "t.me/" in query or query.isdigit():
        username = query.replace("https://", "").replace("http://", "").replace("t.me/", "")
        if username.startswith("@"): username = username[1:]
        try:
            user = await user_app.get_users(int(username) if username.isdigit() else username)
            if not user: return JSONResponse({"error": "Пользователь не найден"})
            result_data = {
                "Имя": f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто",
                "Username": f"@{user.username}" if user.username else "Нет",
                "ID": str(user.id),
                "Подарки": await get_profile_gifts(user.id)
            }
            return JSONResponse({"type": "Telegram Профиль", "result": result_data})
        except Exception as e: return JSONResponse({"error": "Не удалось получить данные"})

    return JSONResponse({"error": "Неизвестный формат"})

def run_fastapi():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), log_level="warning")

# ==========================================
# БОТ
# ==========================================
@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://ваш-сайт.onrender.com")
    await message.reply(
        "👋 Привет! Открой удобный поиск:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Открыть OSINT Поиск", web_app=WebAppInfo(url=url))]])
    )

if __name__ == "__main__":
    print("🚀 Запуск FastAPI...")
    threading.Thread(target=run_fastapi, daemon=True).start()
    print("🤖 Запуск бота...")
    user_app.start()
    bot.run()
