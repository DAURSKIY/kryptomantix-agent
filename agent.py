import os
import requests
import json
import time
from datetime import datetime, timezone

# Берем токены из переменных окружения (в GitHub они будут в секретах)
VK_TOKEN = os.environ.get("VK_TOKEN")
VK_GROUP_ID_STR = os.environ.get("VK_GROUP_ID")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID")

try:
    VK_GROUP_ID = int(VK_GROUP_ID_STR) if VK_GROUP_ID_STR else None
except ValueError:
    VK_GROUP_ID = None

MIN_LIQUIDITY = 50000
MAX_LIQUIDITY = 500000
MIN_AGE_HOURS = 6
MAX_AGE_HOURS = 168
MIN_VOL_LIQ_RATIO = 0.3
MAX_VOL_LIQ_RATIO = 10.0
MAX_POSTS_PER_DAY = 3

STATE_FILE = "agent_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"posted_today": 0, "last_date": "", "posted_addresses": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def get_ai_verdict(cand):
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return "AI-анализ недоступен."
    prompt_text = f"""Ты — опытный криптоаналитик. Напиши краткий вердикт (2-3 предложения) для поста.
    Метрики: Ликвидность ${cand['liquidity']:,.0f}, Объём 24ч ${cand['vol_24h']:,.0f}, Возраст {cand['age_hours']:.1f} ч, Объём/Ликв {cand['ratio']:.2f}x.
    Оцени потенциал. Если объём/ликв > 8x — упомяни накрутку. Если возраст < 24ч — предупреди о риске. Пиши только на русском, без воды."""
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
    except Exception as e:
        print(f"⚠️ Ошибка AI: {e}")
        return "AI-анализ временно недоступен."

def post_to_vk(message):
    url = "https://api.vk.com/method/wall.post"
    params = {"owner_id": VK_GROUP_ID, "from_group": 1, "message": message, "access_token": VK_TOKEN, "v": "5.199"}
    try:
        r = requests.post(url, data=params, timeout=15).json()
        if "response" in r:
            print(f"✅ Пост опубликован! ID: {r['response']['post_id']}")
            return True
        else:
            print(f"❌ Ошибка VK: {r.get('error', {}).get('error_msg')}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    return False

def fetch_new_pools():
    print("🔍 Ищу трендовые токены на Solana...")
    url = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools?page=1"
    try:
        r = requests.get(url, headers={"Accept": "application/json"}, timeout=15)
        r.raise_for_status()
        pools = r.json().get("data", [])
        return [p for p in pools if p.get("attributes", {}).get("reserve_in_usd")]
    except Exception as e:
        print(f"❌ Ошибка GeckoTerminal: {e}")
        return []

def analyze_pool(pool):
    attributes = pool.get("attributes", {})
    name = attributes.get("name", "Unknown")
    address = attributes.get("address", "")
    created_at_str = attributes.get("pool_created_at", "")
    try:
        liquidity = float(attributes.get("reserve_in_usd", 0) or 0)
        vol_24h = float(attributes.get("volume_usd", {}).get("h24", 0) or 0)
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
    return {"name": name, "address": address, "liquidity": liquidity, "vol_24h": vol_24h, "age_hours": age_hours, "ratio": vol_liq_ratio}

def main():
    print("=" * 50)
    print(f"🤖 Агент запущен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if not VK_TOKEN or not VK_GROUP_ID:
        print("❌ ОШИБКА: Нет VK_TOKEN или VK_GROUP_ID.")
        return

    state = load_state()
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_date") != today:
        state["last_date"] = today
        state["posted_today"] = 0
        print("📅 Новый день. Счетчик сброшен.")

    if state["posted_today"] >= MAX_POSTS_PER_DAY:
        print("🛑 Лимит постов на сегодня исчерпан.")
        return

    pools = fetch_new_pools()
    candidates = [analyze_pool(p) for p in pools]
    candidates = [c for c in candidates if c and c["address"] not in state.get("posted_addresses", [])]
    candidates.sort(key=lambda x: x["liquidity"], reverse=True)
    print(f"🎯 Найдено кандидатов: {len(candidates)}")

    posted_count = 0
    for cand in candidates:
        if state["posted_today"] >= MAX_POSTS_PER_DAY: break
        print(f"🧠 Генерирую AI-вердикт для {cand['name']}...")
        ai_text = get_ai_verdict(cand)
        text = f"""🔍 Найден токен с потенциалом

Токен: {cand['name']}
Сеть: Solana
Ликвидность: ${cand['liquidity']:,.0f}
Объём 24ч: ${cand['vol_24h']:,.0f}
Возраст: {cand['age_hours']:.1f} часов
Объём/ликвидность: {cand['ratio']:.2f}x

🤖 Вердикт AI:
{ai_text}

Проверить полный отчет в сканере: https://kryptomantix.streamlit.app

⚠️ Не инвестиционная рекомендация. DYOR."""
        print(f"📝 Постим: {cand['name']}")
        if post_to_vk(text):
            state["posted_today"] += 1
            if "posted_addresses" not in state: state["posted_addresses"] = []
            state["posted_addresses"].append(cand["address"])
            save_state(state)
            posted_count += 1
            time.sleep(3)

    save_state(state)
    print(f"✅ Работа завершена. Опубликовано: {posted_count}")

if __name__ == "__main__":
    main()