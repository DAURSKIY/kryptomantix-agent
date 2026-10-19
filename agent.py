import os
import requests
import json
import time
from datetime import datetime, timezone

# ==========================================
# НАСТРОЙКИ
# ==========================================
VK_TOKEN = os.environ.get("VK_TOKEN")
VK_GROUP_ID_STR = os.environ.get("VK_GROUP_ID")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID")

try:
    VK_GROUP_ID = int(VK_GROUP_ID_STR) if VK_GROUP_ID_STR else None
except ValueError:
    VK_GROUP_ID = None

# ID твоего закрытого чата
VK_CHAT_PEER_ID = 2000000001

# Фильтры
MIN_LIQUIDITY = 30000
MAX_LIQUIDITY = 5000000 
MIN_AGE_HOURS = 6
MAX_AGE_HOURS = 168
MIN_VOL_LIQ_RATIO = 0.3
MAX_VOL_LIQ_RATIO = 10.0
MAX_FDV_LIQ_RATIO = 1000 

# Лимиты: 1 пост на стену и 4 в чат (всего 5 за день)
MAX_WALL_POSTS = 1
MAX_CHAT_POSTS = 4

STATE_FILE = "agent_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    # Обновленный формат состояния
    return {"wall_posts_today": 0, "chat_posts_today": 0, "last_date": "", "posted_addresses": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ==========================================
# AI-ВЕРДИКТ
# ==========================================
def get_ai_verdict(cand):
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return "AI-анализ недоступен."
    
    prompt_text = f"""Ты — опытный криптоаналитик. Оцени токен на основе метрик и напиши краткий вердикт (2-3 предложения).
    Метрики: Название: {cand['name']} в сети {cand['network']}, Ликвидность: ${cand['liquidity']:,.0f}, Объём 24ч: ${cand['vol_24h']:,.0f}, FDV: ${cand['fdv']:,.0f}, Возраст: {cand['age_hours']:.1f} ч, Объём/Ликв: {cand['ratio']:.2f}x.
    Правила: Пиши только на русском, без воды. Оцени риски и потенциал."""
    
    url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"
    headers = {"Content-Type": "application/json", "Authorization": f"Api-Key {YANDEX_API_KEY}"}
    body = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {"stream": False, "temperature": 0.3, "maxTokens": "300"},
        "messages": [{"role": "user", "text": prompt_text}]
    }
    try:
        response = requests.post(url, headers=headers, json=body, timeout=30)
        response.raise_for_status()
        return response.json()["result"]["alternatives"][0]["message"]["text"].strip()
    except Exception:
        return "AI-анализ временно недоступен."

# ==========================================
# ОТПРАВКА
# ==========================================
def post_to_wall(message):
    url = "https://api.vk.com/method/wall.post"
    params = {"owner_id": VK_GROUP_ID, "from_group": 1, "message": message, "access_token": VK_TOKEN, "v": "5.199"}
    try:
        r = requests.post(url, data=params, timeout=15).json()
        if "response" in r:
            print(f"✅ Пост на СТЕНУ опубликован! ID: {r['response']['post_id']}")
            return True
        else:
            print(f"❌ Ошибка VK (стена): {r.get('error', {}).get('error_msg')}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    return False

def send_to_chat(message, peer_id):
    url = "https://api.vk.com/method/messages.send"
    params = {
        "peer_id": peer_id, 
        "message": message, 
        "random_id": int(time.time() * 1000), # Обязательный параметр для ВК
        "access_token": VK_TOKEN, 
        "v": "5.199"
    }
    try:
        r = requests.post(url, data=params, timeout=15).json()
        if "response" in r:
            print(f"✅ Сообщение в ЧАТ отправлено! ID: {r['response']}")
            return True
        else:
            print(f"❌ Ошибка VK (чат): {r.get('error', {}).get('error_msg')}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    return False

# ==========================================
# ПОИСК
# ==========================================
def fetch_new_pools():
    networks = [("solana", "Solana"), ("eth", "Ethereum"), ("bsc", "BSC"), ("base", "Base")]
    all_pools = []
    for net_id, net_name in networks:
        url = f"https://api.geckoterminal.com/api/v2/networks/{net_id}/trending_pools?page=1"
        try:
            r = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
            r.raise_for_status()
            pools = r.json().get("data", [])
            for p in pools:
                if p.get("attributes", {}).get("reserve_in_usd"):
                    p["network_name"] = net_name
                    all_pools.append(p)
            time.sleep(1)
        except Exception: pass
    return all_pools

def analyze_pool(pool):
    attributes = pool.get("attributes", {})
    name = attributes.get("name", "Unknown")
    pool_address = attributes.get("address", "")
    network = pool.get("network_name", "Unknown")
    created_at_str = attributes.get("pool_created_at", "")
    
    try:
        liquidity = float(attributes.get("reserve_in_usd", 0) or 0)
        vol_24h = float(attributes.get("volume_usd", {}).get("h24", 0) or 0)
        fdv = float(attributes.get("fdv_usd", 0) or 0)
    except: return None

    if not created_at_str: return None
    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600
    except: return None

    if age_hours < MIN_AGE_HOURS or age_hours > MAX_AGE_HOURS: return None
    if liquidity < MIN_LIQUIDITY or liquidity > MAX_LIQUIDITY: return None
    
    vol_liq_ratio = vol_24h / liquidity if liquidity > 0 else 0
    if vol_liq_ratio < MIN_VOL_LIQ_RATIO or vol_liq_ratio > MAX_VOL_LIQ_RATIO: return None

    if liquidity > 0 and fdv > 0:
        if (fdv / liquidity) > MAX_FDV_LIQ_RATIO: return None 

    base_token_id = pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id")
    token_address = base_token_id.split("_")[-1] if base_token_id else pool_address

    return {
        "name": name, "address": pool_address, "network": network,
        "token_address": token_address, "fdv": fdv,
        "liquidity": liquidity, "vol_24h": vol_24h, 
        "age_hours": age_hours, "ratio": vol_liq_ratio
    }

# ==========================================
# ЗАПУСК
# ==========================================
def main():
    print(f"🤖 Агент запущен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    
    if state.get("last_date") != today:
        state["last_date"] = today
        state["wall_posts_today"] = 0
        state["chat_posts_today"] = 0
        print("📅 Новый день. Счетчики сброшены.")

    if state["wall_posts_today"] >= MAX_WALL_POSTS and state["chat_posts_today"] >= MAX_CHAT_POSTS:
        print("🛑 Лимиты постов на сегодня исчерпаны.")
        return

    pools = fetch_new_pools()
    candidates = [analyze_pool(p) for p in pools]
    candidates = [c for c in candidates if c and c["address"] not in state.get("posted_addresses", [])]
    candidates.sort(key=lambda x: x["liquidity"], reverse=True)
    
    print(f"🎯 Найдено кандидатов: {len(candidates)}")

    if candidates:
        cand = candidates[0]
        print(f"🧠 Генерирую вердикт для {cand['name']} ({cand['network']})...")
        ai_text = get_ai_verdict(cand)

        base_text = f"""👀 Активность по токену {cand['name']}

🌐 Сеть: {cand['network']}
📜 Контракт: {cand['token_address']}
💧 Ликвидность: ${cand['liquidity']:,.0f}
📊 Объём 24ч: ${cand['vol_24h']:,.0f}
⏳ Возраст: {cand['age_hours']:.1f} ч
⚖️ Объём/Ликв: {cand['ratio']:.2f}x

🤖 AI: {ai_text}
"""
        
        link_text = "\n🔗 Проверить в сканере: kryptomantix.streamlit.app\n\n⚠️ Не ИИР. DYOR."

        # Логика: Если на стене еще не постили, постим туда. Если уже постили, шлем в чат.
        if state["wall_posts_today"] < MAX_WALL_POSTS:
            wall_text = base_text + "\n👇 Как думаете, это гем или скам? Пишите в комментариях!" + link_text
            print(f"📝 Постим на СТЕНУ: {cand['name']}")
            if post_to_wall(wall_text):
                state["wall_posts_today"] += 1
                state["posted_addresses"].append(cand["address"])
                save_state(state)
        
        elif state["chat_posts_today"] < MAX_CHAT_POSTS:
            chat_text = base_text + "\n💎 Эксклюзивный алерт для VK Donut" + link_text
            print(f"📝 Отправляем в VIP-ЧАТ: {cand['name']}")
            if send_to_chat(chat_text, VK_CHAT_PEER_ID):
                state["chat_posts_today"] += 1
                state["posted_addresses"].append(cand["address"])
                save_state(state)

    save_state(state)

if __name__ == "__main__":
    main()
