import os
import sys
import asyncio
import traceback
import uvicorn
import threading

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from pyrogram import Client, filters, idle
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
from tonsdk.utils import Address

API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")
TON_API_KEY = os.environ.get("TON_API_KEY", "")
getcontext().prec = 20

os.makedirs("sessions", exist_ok=True)

bot = Client("sessions/my_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
user_app = Client("sessions/my_user_session", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING, no_updates=True)

app = FastAPI()
class SearchRequest(BaseModel): query: str

@app.get("/", response_class=HTMLResponse)
async def read_index():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()

MONTHS_RU = {1: "январь", 2: "февраль", 3: "март", 4: "апрель", 5: "май", 6: "июнь", 
             7: "июль", 8: "август", 9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь"}

def estimate_registration(user_id):
    db = [
        (100000000, 2013, 8), (200000000, 2014, 1), (300000000, 2014, 6),
        (400000000, 2014, 11), (500000000, 2015, 4), (600000000, 2015, 8),
        (700000000, 2015, 12), (800000000, 2016, 4), (900000000, 2016, 8),
        (1000000000, 2016, 12), (1500000000, 2017, 4), (2000000000, 2017, 7),
        (2500000000, 2017, 12), (3000000000, 2018, 6), (3500000000, 2018, 11),
        (4000000000, 2019, 4), (4500000000, 2019, 9), (5000000000, 2020, 1),
        (5500000000, 2020, 6), (6000000000, 2021, 2), (6500000000, 2021, 8),
        (7000000000, 2022, 6), (7500000000, 2023, 1), (8000000000, 2024, 6),
    ]
    if user_id <= db[0][0]: return f"~ {MONTHS_RU[db[0][2]]} {db[0][1]}"
    if user_id >= db[-1][0]: return f"~ {MONTHS_RU[db[-1][2]]} {db[-1][1]}"
    for i in range(len(db) - 1):
        if db[i][0] <= user_id <= db[i+1][0]:
            id1, y1, m1 = db[i]
            id2, y2, m2 = db[i+1]
            total_months = (y2 - y1) * 12 + (m2 - m1)
            ratio = (user_id - id1) / (id2 - id1)
            add_months = int(ratio * total_months)
            final_month_index = (y1 * 12 + m1 - 1) + add_months
            final_year = final_month_index // 12
            final_month = (final_month_index % 12) + 1
            now = datetime.now()
            years_passed = now.year - final_year
            if now.month < final_month: years_passed -= 1
            year_str = f"{years_passed} год" if years_passed % 10 == 1 and years_passed % 100 != 11 else f"{years_passed} года" if 2 <= years_passed % 10 <= 4 and not (12 <= years_passed % 100 <= 14) else f"{years_passed} лет"
            passed_text = f" ({year_str} назад)" if years_passed > 0 else ""
            return f"~ {MONTHS_RU[final_month]} {final_year}{passed_text}"
    return "Неизвестно"

def to_friendly(addr):
    if not addr or addr == "?": return "?"
    try:
        res = Address(addr).to_string(is_user_friendly=True, is_bounceable=False)
        return res.replace('/', '_').replace('+', '-')
    except Exception:
        return addr

def generate_ton_graph(wallet, senders, receivers):
    G = nx.DiGraph()
    sw = wallet[:6] + "..." + wallet[-4:]
    G.add_node(sw, color='#007aff', size=2000)
    for item in senders[:5]: G.add_edge(item[0][:6]+"..."+item[0][-4:], sw, label=f"{float(item[1]):.2f} TON", color='#4dcd5e')
    for item in receivers[:5]: G.add_edge(sw, item[0][:6]+"..."+item[0][-4:], label=f"{float(item[1]):.2f} TON", color='#e85d5d')
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
            async with session.get(f"{base_url}/blockchain/accounts/{wallet_address}/transactions?limit=100", headers=headers) as r:
                if r.status == 200:
                    for tx in (await r.json()).get("transactions", []):
                        utime = tx.get("utime", 0)
                        if tx.get("in_msg") and tx["in_msg"].get("value"):
                            amt = Decimal(int(tx["in_msg"]["value"])) / Decimal(10**9)
                            if amt >= Decimal('0.1'):
                                total_in += amt
                                src = to_friendly(tx["in_msg"].get("source", {}).get("address", "?"))
                                if src != "?":
                                    events.append((utime, "IN", float(amt), src))
                                    if src not in senders_dict: senders_dict[src] = {'sum': Decimal(0), 'last': utime}
                                    senders_dict[src]['sum'] += amt; senders_dict[src]['last'] = utime
                        for out in tx.get("out_msgs", []):
                            if out.get("value"):
                                amt = Decimal(int(out["value"])) / Decimal(10**9)
                                if amt >= Decimal('0.1'):
                                    total_out += amt
                                    dst = to_friendly(out.get("destination", {}).get("address", "?"))
                                    if dst != "?":
                                        events.append((utime, "OUT", float(amt), dst))
                                        if dst not in receivers_dict: receivers_dict[dst] = {'sum': Decimal(0), 'last': utime}
                                        receivers_dict[dst]['sum'] += amt; receivers_dict[dst]['last'] = utime
            top_senders_list = sorted(senders_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            top_receivers_list = sorted(receivers_dict.items(), key=lambda x: x[1]['sum'], reverse=True)[:5]
            senders_api = [{"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—"} for a, d in top_senders_list]
            receivers_api = [{"addr": a, "amount": f"{d['sum'].normalize():f}", "date": datetime.fromtimestamp(d['last']).strftime("%d.%m.%Y"), "label": "—"} for a, d in top_receivers_list]
            out_pct = (float(total_out) / float(total_in) * 100) if total_in > 0 else 0
            
            # Упрощенный профиль
            profile = {"type": "Кошелек", "conf": "средняя", "desc": "Анализ завершен", "ops": f"{len(events)}", "peers": f"{len(senders_dict) + len(receivers_dict)}", "per_day": "0", "hold": "—", "round": "0%", "typical": "—"}
            
            graph_b64 = generate_ton_graph(wallet_address, top_senders_list, top_receivers_list)
            return {
                "balance": f"{balance.normalize():f}", "status": status, "dns": dns_name,
                "total_in": f"{total_in.normalize():f}", "total_out": f"{total_out.normalize():f}",
                "senders": senders_api, "receivers": receivers_api, "nft": nft_stats, "profile": profile, "graph": graph_b64
            }, None
    except Exception as e:
        return None, str(e)

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
        print(f"[GIFT ERROR] {e}", flush=True)
        return "Ошибка"

@app.post("/api/search")
async def handle_search(req: SearchRequest):
    query = req.query.strip()
    
    if query.endswith(".ton"):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://tonapi.io/v2/dns/{query}") as r:
                    if r.status == 200: query = (await r.json()).get("item", {}).get("address")
                    if not query: return JSONResponse({"error": "Домен не разрешен"})
        except Exception as e: return JSONResponse({"error": f"Ошибка домена: {str(e)}"})
    
    if query.startswith(("UQ", "EQ", "0:")):
        data, error = await analyze_ton_wallet_full(query)
        if error: return JSONResponse({"error": error})
        return JSONResponse({
            "type": "TON Кошелек", "result": {"Баланс": f"{data['balance']} TON", "Метка": data['dns'], "Статус": data['status'], "Всего вход": f"{data['total_in']} TON", "Всего выход": f"{data['total_out']} TON"},
            "senders": data["senders"], "receivers": data["receivers"], "nft": data["nft"], "profile": data["profile"], "graph": data["graph"]
        })

    elif query.startswith("+") and query[1:].isdigit():
        try:
            parsed = phonenumbers.parse(query, "RU")
            operator = carrier.name_for_number(parsed, "ru") or "Неизвестно"
            region = geocoder.description_for_number(parsed, "ru") or "Неизвестно"
            result_data = {"Номер": query.replace("+", ""), "Оператор": operator, "Регион": region, "Telegram": "Не привязан"}
            
            try:
                # Пытаемся найти пользователя по номеру
                user = await user_app.get_users(query)
                if user:
                    result_data["Telegram"] = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Скрыто"
                    result_data["Username"] = f"@{user.username}" if user.username else "Нет"
                    result_data["ID"] = str(user.id)
                    result_data["Регистрация"] = estimate_registration(user.id)
                    result_data["Подарки"] = await get_profile_gifts(user.id)
            except Exception as e:
                # Если ошибка при поиске (например, скрыт или не найден)
                print(f"[PHONE ERROR] {query}: {e}", flush=True)
                result_data["Telegram"] = f"Не найден или скрыт"
                
            return JSONResponse({"type": "Телефон", "result": result_data})
        except Exception as e: 
            return JSONResponse({"error": f"Ошибка телефона: {str(e)}"})

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
                "Регистрация": estimate_registration(user.id),
                "Подарки": await get_profile_gifts(user.id)
            }
            return JSONResponse({"type": "Telegram Профиль", "result": result_data})
        except Exception as e: 
            print(f"[USER ERROR] {username}: {e}", flush=True)
            return JSONResponse({"error": f"Ошибка: {str(e)}"})

    return JSONResponse({"error": "Неизвестный формат"})

@bot.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    url = os.environ.get("RENDER_EXTERNAL_URL", "https://ваш-сайт.onrender.com")
    await message.reply("👋 Привет! Открой удобный поиск:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Открыть OSINT Поиск", web_app=WebAppInfo(url=url))]]))

async def main():
    global bot, user_app
    loop = asyncio.get_running_loop()
    bot.loop = loop
    user_app.loop = loop
    print("🔌 Подключаю user_app...", flush=True)
    try:
        await user_app.start()
        print("✅ user_app подключен", flush=True)
    except Exception as e:
        print(f"❌ Ошибка user_app.start(): {e}", flush=True)
        return
    print("🤖 Запускаю бота...", flush=True)
    try:
        await bot.start()
        print("✅ Бот запущен. Слушаю сообщения...", flush=True)
        await idle()
    except Exception as e:
        print(f"❌ Ошибка bot.start(): {e}", flush=True)

def run_fastapi():
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), log_level="warning")

if __name__ == "__main__":
    threading.Thread(target=run_fastapi, daemon=True).start()
    asyncio.run(main())
