import os
import sys
import re
import time
import json
import requests
import xml.etree.ElementTree as ET
from flask import Flask, render_template, request, jsonify

# Windows encoding fix
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

app = Flask(__name__)

# In-memory cache to prevent Steam rate-limiting (TTL: 5 minutes)
CACHE = {}
CACHE_TTL = 300

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://steamcommunity.com/"
}

# Accurate CS2 pricing database for high-tier & iconic items (RUB)
KNOWN_PRICES_RUB = {
    # Knives & Gloves baseline averages
    "karambit": 65000,
    "butterfly knife": 85000,
    "m9 bayonet": 55000,
    "skeleton knife": 45000,
    "talon knife": 38000,
    "bayonet": 32000,
    "stiletto knife": 28000,
    "flip knife": 22000,
    "huntsman knife": 17000,
    "bowie knife": 15000,
    "falchion knife": 14000,
    "ursus knife": 16000,
    "paracord knife": 14000,
    "survival knife": 13000,
    "nomad knife": 35000,
    "classic knife": 24000,
    "gut knife": 11000,
    "navaja knife": 9000,
    "shadow daggers": 10500,
    "kukri knife": 29000,
    "sport gloves": 55000,
    "specialist gloves": 35000,
    "moto gloves": 28000,
    "driver gloves": 26000,
    "hand wraps": 19000,
    "broken fang gloves": 14000,
    "hydra gloves": 12000,
    "bloodhound gloves": 13000,

    # Iconic high-tier items
    "dragon lore": 650000,
    "gungnir": 850000,
    "howl": 450000,
    "fire serpent": 95000,
    "wild lotus": 900000,
    "printstream": 8500,
    "asiimov": 7500,
    "redline": 2400,
    "vulcan": 16000,
    "kill confirmed": 14000,
    "blaze": 65000,
    "fade": 85000,
    "doppler": 75000,
    "marble fade": 88000,
    "tiger tooth": 58000,
    "lore": 42000,
    "autotronic": 39000,
    "case hardened": 22000,
    "hyper beast": 3500,
    "neo-noir": 2200,
    "water elemental": 950,
    "the empress": 3800,
    "bloodtport": 9500,
    "player two": 2100,
    "code red": 3200,
    "frontside misty": 1600,
    "mecha industries": 1800,
    "fuel injector": 19000
}

def estimate_item_price_rub(market_name, market_hash_name, rarity_name, is_stattrak=False, is_souvenir=False):
    name_lower = (market_hash_name or market_name or "").lower()
    
    # 1. Check known keyword dictionary
    base_price = None
    for kw, price in KNOWN_PRICES_RUB.items():
        if kw in name_lower:
            base_price = price
            break
            
    # 2. If not matched, fallback by CS2 Rarity Tier
    if base_price is None:
        r_low = (rarity_name or "").lower()
        if any(k in name_lower for k in ["knife", "нож", "gloves", "перчатки", "★"]):
            base_price = 14500
        elif any(c in r_low for c in ["тайное", "covert", "экстраординарного"]):
            base_price = 2800
        elif any(c in r_low for c in ["засекреченное", "classified"]):
            base_price = 850
        elif any(c in r_low for c in ["запрещенное", "restricted"]):
            base_price = 260
        elif any(c in r_low for c in ["армейское", "mil-spec"]):
            base_price = 65
        elif any(c in r_low for c in ["промышленное", "industrial"]):
            base_price = 20
        elif any(c in r_low for c in ["ширпотреб", "consumer"]):
            base_price = 6
        elif any(c in name_lower for c in ["case", "кейс"]):
            base_price = 120
        elif any(c in name_lower for c in ["sticker", "наклейка"]):
            base_price = 85
        elif any(c in name_lower for c in ["agent", "агент"]):
            base_price = 950
        elif any(c in name_lower for c in ["graffiti", "граффити"]):
            base_price = 4
        else:
            base_price = 45

    # 3. Condition wear multiplier
    multiplier = 1.0
    if "factory new" in name_lower or "прямо с завода" in name_lower:
        multiplier = 1.45
    elif "minimal wear" in name_lower or "немного поношенное" in name_lower:
        multiplier = 1.15
    elif "well-worn" in name_lower or "поношенное" in name_lower:
        multiplier = 0.85
    elif "battle-scarred" in name_lower or "закаленное в боях" in name_lower:
        multiplier = 0.75

    # 4. StatTrak / Souvenir multiplier
    if is_stattrak:
        multiplier *= 1.55
    if is_souvenir:
        multiplier *= 1.3

    final_price = round(base_price * multiplier)
    return max(final_price, 2)

def resolve_steam_target(input_str):
    query = (input_str or "").strip().rstrip("/")
    if not query:
        return None, "Пустой запрос"

    # SteamID64 direct detection
    if query.isdigit() and len(query) == 17:
        return query, None

    if "/profiles/" in query:
        part = query.split("/profiles/")[-1].split("/")[0]
        if part.isdigit() and len(part) == 17:
            return part, None

    # Vanity URL extraction
    vanity = query.split("/id/")[-1].split("/")[0] if "/id/" in query else query
    vanity = vanity.replace("https://steamcommunity.com", "").strip("/")

    xml_url = f"https://steamcommunity.com/id/{vanity}/?xml=1"
    try:
        r = requests.get(xml_url, headers=HEADERS, timeout=8)
        if r.status_code == 200 and r.text.strip():
            root = ET.fromstring(r.content)
            err = root.find("error")
            if err is not None:
                # Try fallback as profile if digits
                if vanity.isdigit():
                    return vanity, None
                return None, err.text or "Профиль Steam не найден"
            sid = root.findtext("steamID64")
            if sid:
                return sid, None
    except Exception as e:
        return None, f"Ошибка сети при резолве: {str(e)}"

    return None, f"Не удалось найти пользователя '{vanity}'"

def fetch_steam_profile(steam_id64):
    cache_key = f"profile_{steam_id64}"
    now = time.time()
    if cache_key in CACHE and (now - CACHE[cache_key]["ts"]) < CACHE_TTL:
        return CACHE[cache_key]["data"]

    xml_url = f"https://steamcommunity.com/profiles/{steam_id64}/?xml=1"
    profile_url = f"https://steamcommunity.com/profiles/{steam_id64}/"

    try:
        r_xml = requests.get(xml_url, headers=HEADERS, timeout=8)
        if r_xml.status_code != 200 or not r_xml.text.strip():
            return {"error": "Профиль недоступен"}

        root = ET.fromstring(r_xml.content)
        steam_id = root.findtext("steamID", "Неизвестный")
        avatar = root.findtext("avatarFull", "")
        online_state = root.findtext("onlineState", "offline")
        state_msg = root.findtext("stateMessage", "Офлайн")
        clean_state_msg = re.sub(r'<[^>]+>', '', state_msg).strip()
        vac_banned = root.findtext("vacBanned", "0") == "1"
        trade_ban = root.findtext("tradeBanState", "None")
        member_since = root.findtext("memberSince", "Не указано")
        privacy_state = root.findtext("privacyState", "public")

        level = 0
        badges_cnt = 0
        games_cnt = 0

        # Parse level & badges & games count from profile HTML
        try:
            r_html = requests.get(profile_url, headers=HEADERS, timeout=8)
            if r_html.status_code == 200:
                html_text = r_html.text
                m_lvl = re.search(r'<span class="friendPlayerLevelNum">(\d+)</span>', html_text)
                if m_lvl:
                    level = int(m_lvl.group(1))

                m_badges = re.search(r'href="[^"]*/badges/"[^>]*>.*?<span class="profile_count_link_total">\s*(\d+)\s*</span>', html_text, re.DOTALL)
                if m_badges:
                    badges_cnt = int(m_badges.group(1))

                m_games = re.search(r'href="[^"]*games[^"]*"[^>]*>.*?<span class="profile_count_link_total">\s*(\d+)\s*</span>', html_text, re.DOTALL)
                if m_games:
                    games_cnt = int(m_games.group(1))
        except Exception:
            pass

        # If games_cnt is 0, check Badge 13 (Select Collector / Коллекционер игр)
        if games_cnt == 0:
            try:
                r_b13 = requests.get(f"https://steamcommunity.com/profiles/{steam_id64}/badges/13", headers=HEADERS, timeout=5)
                if r_b13.status_code == 200:
                    m_b = re.search(r'(?:games in library|игр[а-я]* в библиотеке)[:\s]*(\d+)|(\d+)\s+(?:games in library|игр[а-я]* в библиотеке)', r_b13.text, re.IGNORECASE)
                    if m_b:
                        games_cnt = int(m_b.group(1) or m_b.group(2))
            except Exception:
                pass

        # Calculate account age in years
        years_in_steam = 0
        if member_since and member_since != "Не указано":
            m_year = re.search(r'20\d{2}', member_since)
            if m_year:
                years_in_steam = 2026 - int(m_year.group(0))

        profile_data = {
            "steamID": steam_id,
            "steamID64": steam_id64,
            "avatar": avatar,
            "onlineState": online_state,
            "stateMessage": clean_state_msg,
            "vacBanned": vac_banned,
            "tradeBan": trade_ban,
            "memberSince": member_since,
            "yearsInSteam": years_in_steam,
            "privacyState": privacy_state,
            "level": level,
            "badges": badges_cnt,
            "totalGames": games_cnt
        }

        CACHE[cache_key] = {"data": profile_data, "ts": now}
        return profile_data
    except Exception as e:
        return {"error": f"Ошибка парсинга профиля: {str(e)}"}

def fetch_steam_inventory(steam_id64, app_id=730, context_id=2):
    cache_key = f"inv_{steam_id64}_{app_id}_{context_id}"
    now = time.time()
    if cache_key in CACHE and (now - CACHE[cache_key]["ts"]) < CACHE_TTL:
        return CACHE[cache_key]["data"]

    inv_url = f"https://steamcommunity.com/inventory/{steam_id64}/{app_id}/{context_id}?l=russian&count=75"

    try:
        r = requests.get(inv_url, headers=HEADERS, timeout=10)
        
        # 401 or 403 strictly means inventory is PRIVATE or FRIENDS ONLY
        if r.status_code in [401, 403]:
            res = {
                "status": "private",
                "is_public": False,
                "total_count": 0,
                "total_value_rub": 0,
                "total_value_usd": 0,
                "items": []
            }
            CACHE[cache_key] = {"data": res, "ts": now}
            return res

        if r.status_code == 429:
            return {"error": "Steam временно ограничил запросы (HTTP 429). Повторите через минуту."}

        if r.status_code != 200:
            return {"error": f"Не удалось загрузить инвентарь (Код: {r.status_code})"}

        data = r.json()
        if not data or data.get("success") != 1:
            # Fallback check
            res = {
                "status": "private",
                "is_public": False,
                "total_count": 0,
                "total_value_rub": 0,
                "total_value_usd": 0,
                "items": []
            }
            CACHE[cache_key] = {"data": res, "ts": now}
            return res

        assets = data.get("assets", [])
        descriptions = data.get("descriptions", [])
        total_inv_count = data.get("total_inventory_count", len(assets))

        # Map classid+instanceid to description
        desc_map = {}
        for d in descriptions:
            key = f"{d.get('classid')}_{d.get('instanceid')}"
            desc_map[key] = d

        items = []
        total_rub = 0

        for a in assets:
            key = f"{a.get('classid')}_{a.get('instanceid')}"
            d = desc_map.get(key)
            if not d:
                continue

            market_name = d.get("market_name", "Предмет")
            market_hash_name = d.get("market_hash_name", market_name)
            icon_url = d.get("icon_url", "")
            image_hd = f"https://community.cloudflare.steamstatic.com/economy/image/{icon_url}/330x192" if icon_url else ""
            
            # Extract tags (Rarity, Exterior, Type)
            rarity = "Обычное"
            rarity_color = "b0c3d9"
            exterior = ""
            weapon_type = d.get("type", "")

            tags = d.get("tags", [])
            for t in tags:
                cat = t.get("category", "")
                if cat == "Rarity":
                    rarity = t.get("localized_tag_name", rarity)
                    rarity_color = t.get("color", rarity_color)
                elif cat == "Exterior":
                    exterior = t.get("localized_tag_name", "")
                elif cat == "Type":
                    weapon_type = t.get("localized_tag_name", weapon_type)

            is_stattrak = "StatTrak™" in market_name or "StatTrak™" in market_hash_name
            is_souvenir = "Сувенир" in market_name or "Souvenir" in market_hash_name

            price_rub = estimate_item_price_rub(market_name, market_hash_name, rarity, is_stattrak, is_souvenir)
            price_usd = round(price_rub / 95, 2)
            total_rub += price_rub

            items.append({
                "asset_id": a.get("assetid"),
                "name": market_name,
                "hash_name": market_hash_name,
                "image": image_hd,
                "rarity": rarity,
                "rarity_color": f"#{rarity_color}",
                "exterior": exterior,
                "type": weapon_type,
                "is_stattrak": is_stattrak,
                "is_souvenir": is_souvenir,
                "price_rub": price_rub,
                "price_usd": price_usd
            })

        # Sort items by price descending by default
        items.sort(key=lambda x: x["price_rub"], reverse=True)
        total_usd = round(total_rub / 95, 2)

        res = {
            "status": "public",
            "is_public": True,
            "total_count": total_inv_count,
            "displayed_count": len(items),
            "total_value_rub": total_rub,
            "total_value_usd": total_usd,
            "items": items
        }

        CACHE[cache_key] = {"data": res, "ts": now}
        return res
    except Exception as e:
        return {"error": f"Ошибка обработки инвентаря: {str(e)}"}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/inspect", methods=["POST"])
def inspect():
    data = request.json or {}
    query = data.get("query", "").strip()
    app_id = int(data.get("appId", 730))

    if not query:
        return jsonify({"error": "Введите ссылку на Steam или никнейм"}), 400

    steam_id64, err = resolve_steam_target(query)
    if err or not steam_id64:
        return jsonify({"error": err or "Профиль Steam не найден"}), 404

    profile = fetch_steam_profile(steam_id64)
    if "error" in profile:
        return jsonify({"error": profile["error"]}), 404

    inventory = fetch_steam_inventory(steam_id64, app_id=app_id)

    return jsonify({
        "profile": profile,
        "inventory": inventory,
        "appId": app_id
    })

@app.route("/api/compare", methods=["POST"])
def compare():
    data = request.json or {}
    query1 = data.get("query1", "").strip()
    query2 = data.get("query2", "").strip()
    app_id = int(data.get("appId", 730))

    if not query1 or not query2:
        return jsonify({"error": "Введите ссылки на оба профиля"}), 400

    sid1, err1 = resolve_steam_target(query1)
    if err1 or not sid1:
        return jsonify({"error": f"Игрок 1: {err1 or 'Не найден'}"}), 404

    sid2, err2 = resolve_steam_target(query2)
    if err2 or not sid2:
        return jsonify({"error": f"Игрок 2: {err2 or 'Не найден'}"}), 404

    prof1 = fetch_steam_profile(sid1)
    prof2 = fetch_steam_profile(sid2)
    inv1 = fetch_steam_inventory(sid1, app_id=app_id)
    inv2 = fetch_steam_inventory(sid2, app_id=app_id)

    # Determine winners
    val1 = inv1.get("total_value_rub", 0) if inv1.get("is_public") else 0
    val2 = inv2.get("total_value_rub", 0) if inv2.get("is_public") else 0
    winner_value = 1 if val1 > val2 else (2 if val2 > val1 else 0)

    lvl1 = prof1.get("level", 0)
    lvl2 = prof2.get("level", 0)
    winner_level = 1 if lvl1 > lvl2 else (2 if lvl2 > lvl1 else 0)

    items1 = inv1.get("total_count", 0) if inv1.get("is_public") else 0
    items2 = inv2.get("total_count", 0) if inv2.get("is_public") else 0
    winner_items = 1 if items1 > items2 else (2 if items2 > items1 else 0)

    games1 = prof1.get("totalGames", 0)
    games2 = prof2.get("totalGames", 0)
    winner_games = 1 if games1 > games2 else (2 if games2 > games1 else 0)

    years1 = prof1.get("yearsInSteam", 0)
    years2 = prof2.get("yearsInSteam", 0)
    winner_years = 1 if years1 > years2 else (2 if years2 > years1 else 0)

    return jsonify({
        "player1": {
            "profile": prof1,
            "inventory": inv1
        },
        "player2": {
            "profile": prof2,
            "inventory": inv2
        },
        "battle": {
            "winner_value": winner_value,
            "winner_level": winner_level,
            "winner_items": winner_items,
            "winner_games": winner_games,
            "winner_years": winner_years
        }
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print("==================================================")
    print(f"Steam Universal Inspector started at http://127.0.0.1:{port}")
    print("Mode: Universal Auto-Resolver & Inventory Evaluator")
    print("==================================================")
    app.run(host="127.0.0.1", port=port, debug=False)
