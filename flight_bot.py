import os
import logging
import re
import json
import threading
import time
import sqlite3
import requests
import csv
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from fast_flights import FlightQuery, Passengers, create_query, get_flights

# --- НАСТРОЙКА ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
USD_TO_RUB = 95.0

# --- FLASK ---
app_web = Flask(__name__)

@app_web.route('/')
def index():
    return "✅ Бот работает!", 200

@app_web.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# --- АВТОПИНГ ---
def keep_alive():
    url = "http://localhost:10000/"
    while True:
        try:
            requests.get(url, timeout=5)
            print("💓 Пинг отправлен, бот активен")
        except:
            pass
        time.sleep(600)

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            priority TEXT DEFAULT 'balance',
            max_stops INTEGER DEFAULT 3,
            preferred_hours TEXT DEFAULT 'all',
            favorite_city TEXT DEFAULT '',
            favorite_airport TEXT DEFAULT '',
            avoid_airports TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            from_city TEXT,
            to_city TEXT,
            date TEXT,
            query_text TEXT,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ База данных инициализирована")

def get_user_preferences(user_id):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT priority, max_stops, preferred_hours, favorite_city, favorite_airport, avoid_airports FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'priority': row[0],
            'max_stops': row[1],
            'preferred_hours': row[2],
            'favorite_city': row[3] or '',
            'favorite_airport': row[4] or '',
            'avoid_airports': row[5] or ''
        }
    return {'priority': 'balance', 'max_stops': 3, 'preferred_hours': 'all', 'favorite_city': '', 'favorite_airport': '', 'avoid_airports': ''}

def save_user_preferences(user_id, preferences):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, priority, max_stops, preferred_hours, favorite_city, favorite_airport, avoid_airports)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, 
          preferences.get('priority', 'balance'),
          preferences.get('max_stops', 3),
          preferences.get('preferred_hours', 'all'),
          preferences.get('favorite_city', ''),
          preferences.get('favorite_airport', ''),
          preferences.get('avoid_airports', '')))
    conn.commit()
    conn.close()
    logger.info(f"✅ Настройки сохранены для user {user_id}")

def save_search_history(user_id, from_city, to_city, date, query_text, result):
    try:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO search_history (user_id, from_city, to_city, date, query_text, result)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, from_city, to_city, date, query_text, json.dumps(result)))
        conn.commit()
        conn.close()
        logger.info(f"✅ История сохранена для user {user_id}: {from_city} → {to_city} {date}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения истории: {e}")
        return False

def get_search_history(user_id, limit=10):
    try:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, from_city, to_city, date, query_text, created_at FROM search_history
            WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit))
        rows = cursor.fetchall()
        conn.close()
        logger.info(f"✅ Получено {len(rows)} записей истории для user {user_id}")
        return rows
    except Exception as e:
        logger.error(f"❌ Ошибка получения истории: {e}")
        return []

def delete_search_history(user_id, history_id=None):
    try:
        conn = sqlite3.connect('users.db')
        cursor = conn.cursor()
        if history_id:
            cursor.execute('DELETE FROM search_history WHERE id = ? AND user_id = ?', (history_id, user_id))
        else:
            cursor.execute('DELETE FROM search_history WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        logger.info(f"✅ История очищена для user {user_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка очистки истории: {e}")
        return False

# --- БАЗА АЭРОПОРТОВ ---
def load_airports():
    city_to_iata = {}
    airport_names = {}
    try:
        with open('airports.dat', 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                airport_name = row[1].strip()
                city = row[2].strip().lower()
                iata = row[4].strip()
                if city and iata:
                    if city not in city_to_iata:
                        city_to_iata[city] = []
                    city_to_iata[city].append(iata)
                    airport_names[iata] = airport_name
        logger.info(f"✅ Загружено {len(city_to_iata)} городов с аэропортами")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки базы аэропортов: {e}")
        city_to_iata, airport_names = get_fallback_data()
    return city_to_iata, airport_names

def get_fallback_data():
    city_to_iata = {
        "москва": ["SVO", "DME", "VKO"],
        "moscow": ["SVO", "DME", "VKO"],
        "лондон": ["LHR", "LCY", "STN", "LGW"],
        "london": ["LHR", "LCY", "STN", "LGW"],
        "нью-йорк": ["JFK", "EWR", "LGA"],
        "new york": ["JFK", "EWR", "LGA"],
    }
    airport_names = {
        "SVO": "Шереметьево",
        "DME": "Домодедово",
        "VKO": "Внуково",
        "LHR": "Хитроу",
        "LCY": "Лондон-Сити",
        "STN": "Станстед",
        "LGW": "Гатвик",
        "JFK": "Кеннеди",
        "EWR": "Ньюарк",
        "LGA": "Ла-Гуардия",
    }
    return city_to_iata, airport_names

CITY_TO_IATA, AIRPORT_NAMES = load_airports()

# --- КОНВЕРТЕР НАЗВАНИЙ ГОРОДОВ (РУССКИЙ → АНГЛИЙСКИЙ) ---
CITY_NAME_CONVERTER = {
    "москва": "moscow",
    "moscow": "moscow",
    "спб": "saint petersburg",
    "санкт-петербург": "saint petersburg",
    "saint petersburg": "saint petersburg",
    "st petersburg": "saint petersburg",
    "екатеринбург": "yekaterinburg",
    "yekaterinburg": "yekaterinburg",
    "новосибирск": "novosibirsk",
    "novosibirsk": "novosibirsk",
    "владивосток": "vladivostok",
    "vladivostok": "vladivostok",
    "сочи": "sochi",
    "sochi": "sochi",
    "казань": "kazan",
    "kazan": "kazan",
    "ростов": "rostov",
    "rostov": "rostov",
    "краснодар": "krasnodar",
    "krasnodar": "krasnodar",
    "самара": "samara",
    "samara": "samara",
    "уфа": "ufa",
    "ufa": "ufa",
    "пермь": "perm",
    "perm": "perm",
    "волгоград": "volgograd",
    "volgograd": "volgograd",
    "нижний новгород": "nizhny novgorod",
    "nizhny novgorod": "nizhny novgorod",
    "лондон": "london",
    "london": "london",
    "париж": "paris",
    "paris": "paris",
    "берлин": "berlin",
    "berlin": "berlin",
    "рим": "rome",
    "rome": "rome",
    "мадрид": "madrid",
    "madrid": "madrid",
    "барселона": "barcelona",
    "barcelona": "barcelona",
    "милан": "milan",
    "milan": "milan",
    "вен": "vienna",
    "vienna": "vienna",
    "прага": "prague",
    "prague": "prague",
    "варшава": "warsaw",
    "warsaw": "warsaw",
    "будапешт": "budapest",
    "budapest": "budapest",
    "амстердам": "amsterdam",
    "amsterdam": "amsterdam",
    "брюссель": "brussels",
    "brussels": "brussels",
    "осло": "oslo",
    "oslo": "oslo",
    "стокгольм": "stockholm",
    "stockholm": "stockholm",
    "копенгаген": "copenhagen",
    "copenhagen": "copenhagen",
    "хельсинки": "helsinki",
    "helsinki": "helsinki",
    "афины": "athens",
    "athens": "athens",
    "лисабон": "lisbon",
    "lisbon": "lisbon",
    "дублин": "dublin",
    "dublin": "dublin",
    "стамбул": "istanbul",
    "istanbul": "istanbul",
    "дубай": "dubai",
    "dubai": "dubai",
    "токио": "tokyo",
    "tokyo": "tokyo",
    "сеул": "seoul",
    "seoul": "seoul",
    "сингапур": "singapore",
    "singapore": "singapore",
    "бангкок": "bangkok",
    "bangkok": "bangkok",
    "пекин": "beijing",
    "beijing": "beijing",
    "шанхай": "shanghai",
    "shanghai": "shanghai",
    "гонконг": "hong kong",
    "hong kong": "hong kong",
    "тайбэй": "taipei",
    "taipei": "taipei",
    "джакарта": "jakarta",
    "jakarta": "jakarta",
    "куала-лумпур": "kuala lumpur",
    "kuala lumpur": "kuala lumpur",
    "манила": "manila",
    "manila": "manila",
    "ханой": "hanoi",
    "hanoi": "hanoi",
    "хошимин": "ho chi minh city",
    "ho chi minh city": "ho chi minh city",
    "мумбаи": "mumbai",
    "mumbai": "mumbai",
    "дель": "delhi",
    "delhi": "delhi",
    "нью-йорк": "new york",
    "new york": "new york",
    "лос-анджелес": "los angeles",
    "los angeles": "los angeles",
    "чикаго": "chicago",
    "chicago": "chicago",
    "майами": "miami",
    "miami": "miami",
    "торонто": "toronto",
    "toronto": "toronto",
    "ванкувер": "vancouver",
    "vancouver": "vancouver",
    "мехико": "mexico city",
    "mexico city": "mexico city",
    "сан-франциско": "san francisco",
    "san francisco": "san francisco",
    "бостон": "boston",
    "boston": "boston",
    "вашингтон": "washington",
    "washington": "washington",
    "рио-де-жанейро": "rio de janeiro",
    "rio de janeiro": "rio de janeiro",
    "буэнос-айрес": "buenos aires",
    "buenos aires": "buenos aires",
    "сидней": "sydney",
    "sydney": "sydney",
    "мельбурн": "melbourne",
    "melbourne": "melbourne",
    "окленд": "auckland",
    "auckland": "auckland",
    "кейптаун": "cape town",
    "cape town": "cape town",
    "каир": "cairo",
    "cairo": "cairo",
    "найроби": "nairobi",
    "nairobi": "nairobi",
}

def normalize_city_name(city_name):
    if not city_name:
        return city_name
    city_lower = city_name.strip().lower()
    if len(city_lower) == 3 and city_name.isupper():
        return city_lower
    if city_lower in CITY_NAME_CONVERTER:
        return CITY_NAME_CONVERTER[city_lower]
    return city_lower

def find_city_code(city_name):
    """Возвращает ВСЕ IATA-коды аэропортов города"""
    if not city_name:
        return []
    normalized = normalize_city_name(city_name)
    city_lower = normalized.lower().strip()
    if len(city_lower) == 3 and city_name.isupper():
        return [city_lower.upper()]
    if city_lower in CITY_TO_IATA:
        return CITY_TO_IATA[city_lower]
    results = []
    for city, codes in CITY_TO_IATA.items():
        if city_lower in city or city in city_lower:
            results.extend(codes)
    return list(set(results))

def get_airport_name(iata_code):
    return AIRPORT_NAMES.get(iata_code, iata_code)

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
MONTHS_RU = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
}

WEEKDAYS_RU = {
    0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'
}

# --- АКТУАЛЬНЫЙ СПИСОК ГОРОДОВ ---
CITIES = {
    "Стамбул": "IST",
    "Дубай": "DXB",
    "Пекин": "PEK",
    "Шанхай": "PVG",
    "Бангкок": "BKK",
    "Анталья": "AYT",
    "Ереван": "EVN",
    "Астана": "NQZ",
    "Ташкент": "TAS",
    "Баку": "GYD",
    "Тбилиси": "TBS",
    "Сочи": "AER",
    "Калининград": "KGD",
    "Санкт-Петербург": "LED",
    "Париж": "CDG",
    "Лондон": "LHR",
}

def reset_webhook():
    try:
        from telegram import Bot
        bot = Bot(TOKEN)
        bot.delete_webhook()
        print("✅ Вебхук сброшен")
    except Exception as e:
        print(f"⚠️ Ошибка сброса вебхука: {e}")

def format_date_with_weekday(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        day = dt.day
        month = MONTHS_RU[dt.month]
        hour = dt.hour
        minute = dt.minute
        weekday = WEEKDAYS_RU[dt.weekday()]
        return f"{day} {month} ({weekday}), {hour:02d}:{minute:02d}"
    except:
        return date_str

def format_duration(minutes):
    if minutes == 'N/A' or minutes is None:
        return 'N/A'
    try:
        mins = int(minutes)
        hours = mins // 60
        mins_remain = mins % 60
        if hours > 0 and mins_remain > 0:
            return f"{hours}ч {mins_remain}м"
        elif hours > 0:
            return f"{hours}ч"
        else:
            return f"{mins_remain}м"
    except:
        return str(minutes)

def parse_single_flight(seg_str):
    result = {
        'from_airport': 'N/A', 'from_code': 'N/A',
        'to_airport': 'N/A', 'to_code': 'N/A',
        'departure': 'N/A', 'arrival': 'N/A', 'duration': 'N/A',
        'departure_hour': 12
    }
    try:
        from_match = re.search(r"from_airport=Airport\(name='([^']+)', code='([^']+)'\)", seg_str)
        if from_match:
            result['from_airport'] = from_match.group(1)
            result['from_code'] = from_match.group(2)
        to_match = re.search(r"to_airport=Airport\(name='([^']+)', code='([^']+)'\)", seg_str)
        if to_match:
            result['to_airport'] = to_match.group(1)
            result['to_code'] = to_match.group(2)
        dep_match = re.search(r"departure=SimpleDatetime\(date=\[(\d+), (\d+), (\d+)\], time=\[(\d+), (\d+)\]\)", seg_str)
        if dep_match:
            year, month, day = dep_match.group(1), dep_match.group(2), dep_match.group(3)
            hour, minute = dep_match.group(4), dep_match.group(5)
            result['departure'] = f"{year}-{month.zfill(2)}-{day.zfill(2)} {hour.zfill(2)}:{minute.zfill(2)}"
            result['departure_hour'] = int(hour)
        arr_match = re.search(r"arrival=SimpleDatetime\(date=\[(\d+), (\d+), (\d+)\], time=\[(\d+), (\d+)\]\)", seg_str)
        if arr_match:
            year, month, day = arr_match.group(1), arr_match.group(2), arr_match.group(3)
            hour, minute = arr_match.group(4), arr_match.group(5)
            result['arrival'] = f"{year}-{month.zfill(2)}-{day.zfill(2)} {hour.zfill(2)}:{minute.zfill(2)}"
        dur_match = re.search(r"duration=(\d+)", seg_str)
        if dur_match:
            result['duration'] = int(dur_match.group(1))
    except:
        pass
    return result

def parse_flight_data(result):
    flights_data = []
    for flight in result:
        try:
            price_usd = getattr(flight, 'price', 'N/A')
            airlines = getattr(flight, 'airlines', [])
            airline = airlines[0] if airlines else 'N/A'
            flight_list = getattr(flight, 'flights', [])
            segments = []
            total_duration = 0
            for seg in flight_list:
                seg_str = str(seg)
                parsed = parse_single_flight(seg_str)
                segments.append(parsed)
                if parsed.get('duration'):
                    total_duration += parsed['duration']
            flights_data.append({
                'airline': airline,
                'price_usd': price_usd,
                'segments': segments,
                'total_segments': len(segments),
                'total_duration': total_duration,
                'stops': len(segments) - 1
            })
        except Exception as e:
            logging.error(f"Error parsing flight: {e}")
            continue
    return flights_data

# --- СИСТЕМА ОЦЕНКИ ---
def rate_flight(flight, user_preferences):
    score = 0
    price = flight['price_usd']
    stops = flight['stops']
    total_duration = flight['total_duration']
    
    if price < 200:
        score += 30
    elif price < 400:
        score += 25
    elif price < 600:
        score += 20
    elif price < 800:
        score += 15
    else:
        score += 10
    
    max_stops = user_preferences.get('max_stops', 3)
    if stops <= max_stops:
        if stops == 0:
            score += 30
        elif stops == 1:
            score += 20
        elif stops == 2:
            score += 10
        else:
            score += 5
    else:
        score -= 10
    
    if len(flight['segments']) > 0:
        dep_hour = flight['segments'][0].get('departure_hour', 12)
        pref_hours = user_preferences.get('preferred_hours', 'all')
        
        if pref_hours == 'morning' and 6 <= dep_hour <= 12:
            score += 20
        elif pref_hours == 'day' and 12 <= dep_hour <= 18:
            score += 20
        elif pref_hours == 'evening' and 18 <= dep_hour <= 23:
            score += 20
        elif pref_hours == 'night' and (dep_hour >= 23 or dep_hour <= 6):
            score += 20
        elif pref_hours == 'all':
            if 8 <= dep_hour <= 20:
                score += 15
            else:
                score += 5
        else:
            if 8 <= dep_hour <= 20:
                score += 15
            else:
                score += 5
    
    if total_duration < 180:
        score += 20
    elif total_duration < 360:
        score += 15
    elif total_duration < 600:
        score += 10
    else:
        score += 5
    
    priority = user_preferences.get('priority', 'balance')
    if priority == 'price':
        score = score * 0.6 + max(0, (100 - price / 5)) * 0.4
    elif priority == 'speed':
        score = score * 0.6 + max(0, (100 - total_duration / 6)) * 0.4
    elif priority == 'comfort':
        comfort_score = 100 - stops * 20
        score = score * 0.5 + comfort_score * 0.5
    
    return min(100, max(0, score))

def format_flight_card(flight, index=None):
    price_usd = flight['price_usd']
    price_rub = int(price_usd * USD_TO_RUB) if price_usd != 'N/A' else 'N/A'
    
    card = ""
    if index:
        card += f"*{index}.* "
    
    card += f"✈️ *{flight['airline']}* — {price_rub} ₽ (${price_usd})\n"
    
    for j, seg in enumerate(flight['segments'], 1):
        dep = format_date_with_weekday(seg['departure']) if seg['departure'] != 'N/A' else 'N/A'
        arr = format_date_with_weekday(seg['arrival']) if seg['arrival'] != 'N/A' else 'N/A'
        dur = format_duration(seg['duration'])
        from_name = get_airport_name(seg['from_code'])
        to_name = get_airport_name(seg['to_code'])
        card += f"   {from_name} ({seg['from_code']}) → {to_name} ({seg['to_code']})  🛫 {dep}  🛬 {arr}  ⏱ {dur}\n"
    
    stops = flight['stops']
    if stops == 0:
        card += f"   🟢 *Прямой рейс*"
    else:
        card += f"   🔄 *{stops} пересадки*"
    
    return card

def get_best_flights(flights_data, user_preferences):
    if not flights_data:
        return None, None, None
    
    max_stops = user_preferences.get('max_stops', 3)
    filtered = [f for f in flights_data if f['stops'] <= max_stops]
    
    if not filtered:
        filtered = flights_data
    
    for flight in filtered:
        flight['score'] = rate_flight(flight, user_preferences)
    
    best_overall = max(filtered, key=lambda x: x['score']) if filtered else None
    cheapest = min(filtered, key=lambda x: x['price_usd']) if filtered else None
    fastest = min(filtered, key=lambda x: x['total_duration']) if filtered else None
    
    return best_overall, cheapest, fastest

def get_sorted_flights(flights_data, user_preferences, favorite_airport=None):
    if not flights_data:
        return []
    
    max_stops = user_preferences.get('max_stops', 3)
    filtered = [f for f in flights_data if f['stops'] <= max_stops]
    
    if not filtered:
        filtered = flights_data
    
    if favorite_airport:
        def sort_key(flight):
            is_favorite = 0
            if len(flight['segments']) > 0:
                from_code = flight['segments'][0].get('from_code', '')
                if from_code == favorite_airport:
                    is_favorite = -1
            return (is_favorite, flight['price_usd'])
        filtered.sort(key=lambda x: (0 if len(x['segments']) > 0 and x['segments'][0].get('from_code', '') != favorite_airport else -1, x['price_usd']))
        return filtered
    
    for flight in filtered:
        flight['score'] = rate_flight(flight, user_preferences)
    
    priority = user_preferences.get('priority', 'balance')
    
    if priority == 'price':
        sorted_flights = sorted(filtered, key=lambda x: x['price_usd'])
    elif priority == 'speed':
        sorted_flights = sorted(filtered, key=lambda x: x['total_duration'])
    elif priority == 'comfort':
        sorted_flights = sorted(filtered, key=lambda x: x['stops'])
    else:
        sorted_flights = sorted(filtered, key=lambda x: x['score'], reverse=True)
    
    return sorted_flights

def get_reason(flight, prefs):
    reasons = []
    if flight['stops'] == 0:
        reasons.append("✈️ прямой рейс без пересадок")
    elif flight['stops'] == 1:
        reasons.append("🔄 только 1 пересадка")
    price = flight['price_usd']
    if price < 300:
        reasons.append("💰 отличная цена")
    elif price < 500:
        reasons.append("💰 хорошая цена")
    duration = flight['total_duration']
    if duration < 180:
        reasons.append("⚡ очень быстро")
    elif duration < 360:
        reasons.append("⚡ быстро")
    priority = prefs.get('priority', 'balance')
    if priority == 'price':
        reasons.append("📊 лучшая цена среди вариантов")
    elif priority == 'speed':
        reasons.append("📊 самый быстрый вариант")
    elif priority == 'comfort':
        reasons.append("📊 оптимальный комфорт")
    else:
        reasons.append("📊 оптимальный баланс цены и комфорта")
    return "✅ " + ", ".join(reasons[:3])

# --- КНОПКИ ---
def get_main_keyboard():
    buttons = [
        [KeyboardButton("✈️ Начать поиск")],
        [KeyboardButton("⚙️ Настройки"), KeyboardButton("📊 История")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_city_keyboard(user_id=None):
    buttons = []
    row = []
    
    favorite = ""
    if user_id:
        prefs = get_user_preferences(user_id)
        favorite = prefs.get('favorite_city', '')
    
    city_items = list(CITIES.items())
    if favorite and favorite in CITIES.values():
        fav_name = None
        for name, code in CITIES.items():
            if code == favorite:
                fav_name = name
                break
        if fav_name:
            city_items = [(name, code) for name, code in city_items if code != favorite]
            city_items.insert(0, (f"⭐ {fav_name}", favorite))
    
    for i, (name, code) in enumerate(city_items):
        row.append(InlineKeyboardButton(f"{name} ({code})", callback_data=f"city_{code}"))
        if (i + 1) % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    
    buttons.append([InlineKeyboardButton("🔍 Поиск по городу", callback_data="search_by_city")])
    buttons.append([InlineKeyboardButton("✈️ Популярные маршруты", callback_data="popular_routes")])
    return InlineKeyboardMarkup(buttons)

def get_date_keyboard():
    today = datetime.now().date()
    buttons = [
        [InlineKeyboardButton(f"📅 Сегодня ({today.strftime('%d.%m')})", callback_data=f"date_{today.strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton(f"📅 Завтра ({(today + timedelta(days=1)).strftime('%d.%m')})", callback_data=f"date_{(today + timedelta(days=1)).strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton(f"📅 Через неделю ({(today + timedelta(days=7)).strftime('%d.%m')})", callback_data=f"date_{(today + timedelta(days=7)).strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton("✏️ Ввести дату вручную", callback_data="manual_date")],
    ]
    return InlineKeyboardMarkup(buttons)

def get
