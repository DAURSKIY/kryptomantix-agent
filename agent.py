import os
import requests
import json
import time
from datetime import datetime, timezone
import mimetypes

VK_TOKEN = os.environ.get("VK_TOKEN")
VK_GROUP_ID_STR = os.environ.get("VK_GROUP_ID")
YANDEX_API_KEY = os.environ.get("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.environ.get("YANDEX_FOLDER_ID")

try:
    VK_GROUP_ID = int(VK_GROUP_ID_STR) if VK_GROUP_ID_STR else None
except ValueError:
    VK_GROUP_ID = None

MIN_LIQUIDITY = 30000
MAX_LIQUIDITY = 5000000 
MIN_AGE_HOURS = 6
MAX_AGE_HOURS = 168
MIN_VOL_LIQ_RATIO = 0.3
MAX_VOL_LIQ_RATIO = 10.0
MAX_POSTS_PER_DAY = 5 
STATE_FILE = "agent_state.json"

DEFAULT_IMAGE = "https://raw.githubusercontent.com/DAURSKIY/kryptomantix/main/favicon.png"

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return {"posted_today": 0, "last_date": "", "posted_addresses": []}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def upload_photo_to_vk(image_url):
    if not image_url: return None
    try:
        print(f"⬇️ Скачиваю картинку: {image_url[:50]}...")
        img_resp = requests.get(image_url, timeout=15)
        img_data = img_resp.content
        
        # Определяем формат файла (png, jpg и т.д.)
        content_type = img_resp.headers.get('Content-Type', 'image/jpeg')
        ext = mimetypes.guess_extension(content_type) or '.jpg'
        if ext == '.jpe': ext = '.jpg'

        print("⬆️ Запрашиваю сервер для загрузки в ВК...")
        url = f"https://api.vk.com/method/photos.getWallUploadServer?group_id={abs(VK_GROUP_ID)}&access_token={VK_TOKEN}&v=5.199"
        r = requests.get(url, timeout=10).json()
        if "error" in r: 
            print(f"❌ VK (getWallUploadServer): {r['error']['error_msg']}")
            return None
        upload_url = r["response"]["upload_url"]

        print("⬆️ Отправляю картинку на сервер ВК...")
        files = {'photo': (f'image{ext}', img_data, content_type)}
        r_upload = requests.post(upload_url, files=files, timeout=20).json()

        if "photo" not in r_upload or r_upload["photo"] == "[]":
            print(f"❌ VK не принял файл: {r_upload}")
            return None

        print("💾 Сохраняю картинку на стене...")
        params = {
            "group_id": abs(VK_GROUP_ID),
            "photo": r_upload["photo"],
            "server": r_upload["server"],
            "hash": r_upload["hash"],
            "access_token": VK_TOKEN,
            "v": "5.199"
        }
        r_save = requests.post("https://api.vk.com/method/photos.saveWallPhoto", data=params, timeout=10).json()
        
        if "response" in r_save:
            p = r_save["response"][0]
            photo_id = f"photo{p['owner_id']}_{p['id']}"
            print(f"✅ Картинка готова! ID: {photo_id}")
            return photo_id
        else:
            print(f"❌ VK (saveWallPhoto): {r_save.get('error', {}).get('error_msg')}")
            
    except Exception as e:
        print(f"❌ Ошибка при работе с фото: {e}")
    return None

def get_ai_verdict(cand):
    if not YANDEX_API_KEY or not YANDEX_FOLDER_ID:
        return "AI-анализ недоступен."
    prompt_text = f"""Ты — опытный криптоаналитик. Напиши краткий вердикт (2-3 предложения).
    Токен: {cand['name']} в сети {cand['network']}. Ликвидность ${cand['liquidity']:,.0f}, Объём 24ч ${cand['vol_24h']:,.0f}, Возраст {cand['age_hours']:.1f} ч, Объём/Ликв {cand['ratio']:.2f}x.
    Оцени потенциал. Пиши только на русском, без воды."""
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
        return "AI-анализ временно недоступен."

def post_to_vk(message, photo_id=None):
    url = "https://api.vk.com/method/wall.post"
    params = {
        "owner_id": VK_GROUP_ID, "from_group": 1, 
        "message": message, "access_token": VK_TOKEN, "v": "5.199"
    }
    if photo_id:
        params["attachments"] = photo_id

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
        except Exception:
            pass
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

    base_token_id = pool.get("relationships", {}).get("base_token", {}).get("data", {}).get("id")
    token_address = base_token_id.split("_")[-1] if base_token_id else None

    return {
        "name": name, "address": pool_address, "network": network,
        "token_address": token_address,
        "liquidity": liquidity, "vol_24h": vol_24h, 
        "age_hours": age_hours, "ratio": vol_liq_ratio
    }

def get_token_image(token_address):
    if not token_address: return DEFAULT_IMAGE
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}", timeout=10).json()
        pairs = r.get("pairs")
        if pairs and len(pairs) > 0:
            img = pairs[0].get("info", {}).get("imageUrl")
            if img: return img
    except: pass
    return DEFAULT_IMAGE

def main():
    print(f"🤖 Агент запущен: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    state = load_state()
    
    today = datetime.now().strftime("%Y-%m-%d")
    if state.get("last_date") != today:
        state["last_date"] = today
        state["posted_today"] = 0

    if state["posted_today"] >= MAX_POSTS_PER_DAY:
        print("🛑 Лимит постов на сегодня исчерпан.")
        return

    pools = fetch_new_pools()
    candidates = [analyze_pool(p) for p in pools]
    candidates = [c for c in candidates if c and c["address"] not in state.get("posted_addresses", [])]
    candidates.sort(key=lambda x: x["liquidity"], reverse=True)
    
    print(f"🎯 Найдено кандидатов: {len(candidates)}")

    if candidates:
        cand = candidates[0]
        print(f"🧠 Генерирую вердикт для {cand['name']}...")
        ai_text = get_ai_verdict(cand)
        
        img_url = get_token_image(cand.get("token_address"))
        photo_id = upload_photo_to_vk(img_url)

        text = f"""👀 Заметили активность по токену {cand['name']}

🌐 Сеть: {cand['network']}
💧 Ликвидность: ${cand['liquidity']:,.0f}
📊 Объём за 24ч: ${cand['vol_24h']:,.0f}
⏳ Возраст: {cand['age_hours']:.1f} часов
⚖️ Объём/Ликв: {cand['ratio']:.2f}x

🤖 Мнение AI: {ai_text}

🔗 Полный разбор в нашем сканере: kryptomantix.streamlit.app

⚠️ Не ИИР (не инвестиционная рекомендация). DYOR."""

        if post_to_vk(text, photo_id):
            state["posted_today"] += 1
            state["posted_addresses"].append(cand["address"])
            save_state(state)

    save_state(state)

if __name__ == "__main__":
    main()
