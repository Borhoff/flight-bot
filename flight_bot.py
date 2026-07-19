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

# --- TRAVELPAYOUTS / AVIASALES ---
TRAVELPAYOUTS_TOKEN = "eb631f12ac7f83fda4125614a6dd04bc"

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

# --- AVIASALES / TRAVELPAYOUTS GRAPHQL API ---
def search_aviasales(origin, destination, date):
    """Поиск билетов через Travelpayouts GraphQL API"""
    try:
        url = "https://api.travelpayouts.com/graphql"
        
        query = """
        query PricesOneWay($origin: String!, $destination: String!, $departDate: String!) {
            prices_one_way(
                params: {
                    origin: $origin
                    destination: $destination
                    depart_date: $departDate
                }
                sorting: VALUE_ASC
                limit: 100
            ) {
                departure_at
                return_at
                value
                airline
                flight_number
                ticket_link
                transfers
            }
        }
        """
        
        variables = {
            "origin": origin,
            "destination": destination,
            "departDate": date
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-Access-Token": TRAVELPAYOUTS_TOKEN
        }
        
        payload = {
            "query": query,
            "variables": variables
        }
        
        logger.info(f"📡 Aviasales запрос: {origin}→{destination} {date}")
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        logger.info(f"📡 Aviasales статус: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if "errors" in data:
                logger.error(f"❌ Aviasales ошибка: {data['errors']}")
                return None
            logger.info(f"✅ Aviasales ответ получен")
            return data.get("data", {})
        else:
            logger.error(f"❌ Aviasales HTTP ошибка: {response.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"❌ Aviasales ошибка: {e}")
        return None

def parse_aviasales_result(data, origin, destination, date):
    """Парсит ответ Aviasales GraphQL в единый формат"""
    if not data:
        logger.warning(f"⚠️ Aviasales: нет данных для парсинга")
        return []
    
    flights = []
    try:
        prices = data.get("prices_one_way", [])
        logger.info(f"📊 Aviasales: найдено {len(prices)} цен")
        
        for item in prices:
            airline = item.get("airline", "N/A")
            price = item.get("value", 0) / 100
            departure_at = item.get("departure_at", "N/A")
            return_at = item.get("return_at", "N/A")
            flight_number = item.get("flight_number", "")
            transfers = item.get("transfers", 0)
            ticket_link = item.get("ticket_link", "")
            
            dep_time = "N/A"
            arr_time = "N/A"
            dep_hour = 12
            try:
                if departure_at != "N/A":
                    dt = datetime.fromisoformat(departure_at.replace("Z", "+00:00"))
                    dep_time = dt.strftime("%Y-%m-%d %H:%M")
                    dep_hour = dt.hour
                if return_at != "N/A":
                    dt = datetime.fromisoformat(return_at.replace("Z", "+00:00"))
                    arr_time = dt.strftime("%Y-%m-%d %H:%M")
            except:
                pass
            
            flights.append({
                'airline': airline,
                'price_usd': price,
                'segments': [
                    {
                        'from_code': origin,
                        'to_code': destination,
                        'departure': dep_time,
                        'arrival': arr_time,
                        'duration': 0,
                        'departure_hour': dep_hour
                    }
                ],
                'total_segments': 1,
                'total_duration': 0,
                'stops': transfers,
                'flight_number': flight_number,
                'ticket_link': ticket_link
            })
            logger.info(f"✈️ Aviasales: найден рейс {airline} {flight_number} за {price} USD")
    except Exception as e:
        logger.error(f"❌ Aviasales парсинг ошибка: {e}")
    
    return flights

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
        "дубай": ["DXB"],
        "dubai": ["DXB"],
        "лондон": ["LHR", "LGW", "STN"],
        "london": ["LHR", "LGW", "STN"],
        "нью-йорк": ["JFK", "EWR", "LGA"],
        "new york": ["JFK", "EWR", "LGA"],
        "париж": ["CDG", "ORY"],
        "paris": ["CDG", "ORY"],
        "стамбул": ["IST"],
        "istanbul": ["IST"],
        "пекин": ["PEK"],
        "beijing": ["PEK"],
        "шанхай": ["PVG"],
        "shanghai": ["PVG"],
        "бангкок": ["BKK"],
        "bangkok": ["BKK"],
        "анталья": ["AYT"],
        "antalya": ["AYT"],
        "ереван": ["EVN"],
        "yerevan": ["EVN"],
        "астана": ["NQZ"],
        "astana": ["NQZ"],
        "ташкент": ["TAS"],
        "tashkent": ["TAS"],
        "баку": ["GYD"],
        "baku": ["GYD"],
        "тбилиси": ["TBS"],
        "tbilisi": ["TBS"],
        "сочи": ["AER"],
        "sochi": ["AER"],
        "калининград": ["KGD"],
        "kaliningrad": ["KGD"],
        "санкт-петербург": ["LED"],
        "saint petersburg": ["LED"],
    }
    airport_names = {
        "SVO": "Шереметьево",
        "DME": "Домодедово",
        "VKO": "Внуково",
        "DXB": "Дубай",
        "DWC": "Дубай-Аль-Мактум",
        "LHR": "Хитроу",
        "LGW": "Гатвик",
        "STN": "Станстед",
        "JFK": "Кеннеди",
        "EWR": "Ньюарк",
        "LGA": "Ла-Гуардия",
        "CDG": "Шарль-де-Голль",
        "ORY": "Орли",
        "IST": "Стамбул",
        "PEK": "Пекин",
        "PVG": "Шанхай Пудун",
        "BKK": "Бангкок",
        "AYT": "Анталья",
        "EVN": "Ереван",
        "NQZ": "Астана",
        "TAS": "Ташкент",
        "GYD": "Баку",
        "TBS": "Тбилиси",
        "AER": "Сочи",
        "KGD": "Калининград",
        "LED": "Санкт-Петербург",
    }
    return city_to_iata, airport_names

CITY_TO_IATA, AIRPORT_NAMES = load_airports()

# --- КОНВЕРТЕР ---
CITY_NAME_CONVERTER = {
    "москва": "moscow",
    "moscow": "moscow",
    "лондон": "london",
    "london": "london",
    "париж": "paris",
    "paris": "paris",
    "стамбул": "istanbul",
    "istanbul": "istanbul",
    "дубай": "dubai",
    "dubai": "dubai",
    "пекин": "beijing",
    "beijing": "beijing",
    "шанхай": "shanghai",
    "shanghai": "shanghai",
    "бангкок": "bangkok",
    "bangkok": "bangkok",
    "анья": "antalya",
    "antalya": "antalya",
    "ереван": "yerevan",
    "yerevan": "yerevan",
    "астана": "astana",
    "astana": "astana",
    "ташкент": "tashkent",
    "tashkent": "tashkent",
    "баку": "baku",
    "baku": "baku",
    "тбилиси": "tbilisi",
    "tbilisi": "tbilisi",
    "сочи": "sochi",
    "sochi": "sochi",
    "калининград": "kaliningrad",
    "kaliningrad": "kaliningrad",
    "санкт-петербург": "saint petersburg",
    "saint petersburg": "saint petersburg",
    "спб": "saint petersburg",
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
    if not city_name:
        return []
    
    normalized = normalize_city_name(city_name)
    city_lower = normalized.lower().strip()
    
    # --- ПОПУЛЯРНЫЕ ГОРОДА (приоритет) ---
    popular_cities = {
        "москва": ["SVO", "DME", "VKO"],
        "moscow": ["SVO", "DME", "VKO"],
        "дубай": ["DXB"],
        "dubai": ["DXB"],
        "лондон": ["LHR", "LGW", "STN"],
        "london": ["LHR", "LGW", "STN"],
        "нью-йорк": ["JFK", "EWR", "LGA"],
        "new york": ["JFK", "EWR", "LGA"],
        "париж": ["CDG", "ORY"],
        "paris": ["CDG", "ORY"],
        "стамбул": ["IST"],
        "istanbul": ["IST"],
        "пекин": ["PEK"],
        "beijing": ["PEK"],
        "шанхай": ["PVG"],
        "shanghai": ["PVG"],
        "бангкок": ["BKK"],
        "bangkok": ["BKK"],
        "анталья": ["AYT"],
        "antalya": ["AYT"],
        "ереван": ["EVN"],
        "yerevan": ["EVN"],
        "астана": ["NQZ"],
        "astana": ["NQZ"],
        "ташкент": ["TAS"],
        "tashkent": ["TAS"],
        "баку": ["GYD"],
        "baku": ["GYD"],
        "тбилиси": ["TBS"],
        "tbilisi": ["TBS"],
        "сочи": ["AER"],
        "sochi": ["AER"],
        "калининград": ["KGD"],
        "kaliningrad": ["KGD"],
        "санкт-петербург": ["LED"],
        "saint petersburg": ["LED"],
    }
    
    if city_lower in popular_cities:
        return popular_cities[city_lower]
    
    # --- IATA-код (3 буквы) ---
    if len(city_lower) == 3 and city_name.isupper():
        return [city_lower.upper()]
    
    # --- ПОИСК В БАЗЕ ---
    if city_lower in CITY_TO_IATA:
        return CITY_TO_IATA[city_lower]
    
    # --- ЧАСТИЧНОЕ СОВПАДЕНИЕ ---
    results = []
    for city, codes in CITY_TO_IATA.items():
        if city_lower in city or city in city_lower:
            results.extend(codes)
    return list(set(results))

def get_airport_name(iata_code):
    return AIRPORT_NAMES.get(iata_code, iata_code)

# --- ВСПОМОГАТЕЛЬНЫЕ ---
MONTHS_RU = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
}

WEEKDAYS_RU = {
    0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'
}

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
    
    # Штраф за долгие пересадки
    if len(flight['segments']) > 1:
        for i in range(len(flight['segments']) - 1):
            try:
                arr1 = datetime.strptime(flight['segments'][i]['arrival'], "%Y-%m-%d %H:%M")
                dep2 = datetime.strptime(flight['segments'][i+1]['departure'], "%Y-%m-%d %H:%M")
                layover = (dep2 - arr1).total_seconds() / 60
                if layover > 480:
                    score -= 10
                elif layover > 240:
                    score -= 5
            except:
                pass
    
    priority = user_preferences.get('priority', 'balance')
    
    if priority == 'price':
        score = score * 0.6 + max(0, (100 - price / 5)) * 0.4
    elif priority == 'speed':
        score = score * 0.6 + max(0, (100 - total_duration / 6)) * 0.4
    elif priority == 'comfort':
        comfort_score = 100 - stops * 20
        score = score * 0.5 + comfort_score * 0.5
    elif priority == 'convenience':
        convenience_score = 100
        convenience_score -= stops * 15
        if len(flight['segments']) > 0:
            dep_hour = flight['segments'][0].get('departure_hour', 12)
            if not (6 <= dep_hour <= 22):
                convenience_score -= 20
        if len(flight['segments']) > 1:
            for i in range(len(flight['segments']) - 1):
                try:
                    arr1 = datetime.strptime(flight['segments'][i]['arrival'], "%Y-%m-%d %H:%M")
                    dep2 = datetime.strptime(flight['segments'][i+1]['departure'], "%Y-%m-%d %H:%M")
                    layover = (dep2 - arr1).total_seconds() / 60
                    if layover > 480:
                        convenience_score -= 20
                    elif layover > 240:
                        convenience_score -= 10
                    elif layover > 120:
                        convenience_score -= 5
                except:
                    pass
        score = score * 0.3 + convenience_score * 0.7
    else:
        score = score * 0.5 + max(0, (100 - price / 8)) * 0.3 + max(0, (100 - total_duration / 8)) * 0.2
    
    return min(100, max(0, score))

def format_flight_card_compact(flight, index=None, label=None):
    price_usd = flight['price_usd']
    price_rub = int(price_usd * USD_TO_RUB) if price_usd != 'N/A' else 'N/A'
    
    card = ""
    if index:
        card += f"*{index}.* "
    if label:
        card += f"{label} "
    
    airline = flight['airline']
    if flight.get('flight_number'):
        airline += f" {flight['flight_number']}"
    card += f"✈️ *{airline}* — {price_rub} ₽ (${price_usd})\n"
    
    first_seg = flight['segments'][0]
    last_seg = flight['segments'][-1]
    
    dep = format_date_with_weekday(first_seg['departure']) if first_seg['departure'] != 'N/A' else 'N/A'
    arr = format_date_with_weekday(last_seg['arrival']) if last_seg['arrival'] != 'N/A' else 'N/A'
    total = format_duration(flight['total_duration'])
    
    card += f"   {first_seg['from_code']} → {last_seg['to_code']}  🛫 {dep}  🛬 {arr}  ⏱ {total}\n"
    
    stops = flight['stops']
    if stops == 0:
        card += f"   🟢 *Прямой рейс*"
    else:
        layover_info = []
        for i in range(stops):
            seg = flight['segments'][i]
            next_seg = flight['segments'][i+1]
            try:
                arr_time = datetime.strptime(seg['arrival'], "%Y-%m-%d %H:%M")
                dep_time = datetime.strptime(next_seg['departure'], "%Y-%m-%d %H:%M")
                layover = (dep_time - arr_time).total_seconds() / 60
                layover_info.append(f"{seg['to_code']} ({format_duration(layover)})")
            except:
                layover_info.append(seg['to_code'])
        card += f"   🔄 *{stops} пересадки:* {', '.join(layover_info)}"
    
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
    elif priority == 'convenience':
        sorted_flights = sorted(filtered, key=lambda x: x['score'], reverse=True)
    else:
        sorted_flights = sorted(filtered, key=lambda x: x['score'], reverse=True)
    
    return sorted_flights

def get_reason_compact(flight, prefs):
    reasons = []
    if flight['stops'] == 0:
        reasons.append("✈️ прямой")
    elif flight['stops'] == 1:
        reasons.append("🔄 1 пересадка")
    price = flight['price_usd']
    if price < 300:
        reasons.append("💰 дешёвый")
    elif price < 500:
        reasons.append("💰 средний")
    duration = flight['total_duration']
    if duration < 180:
        reasons.append("⚡ быстрый")
    elif duration < 360:
        reasons.append("⚡ средний")
    priority = prefs.get('priority', 'balance')
    if priority == 'price':
        reasons.append("📊 цена")
    elif priority == 'speed':
        reasons.append("📊 скорость")
    elif priority == 'comfort':
        reasons.append("📊 комфорт")
    elif priority == 'convenience':
        reasons.append("📊 удобство")
    else:
        reasons.append("📊 баланс")
    return "✅ " + ", ".join(reasons[:3])

def get_priority_keyboard():
    buttons = [
        [
            InlineKeyboardButton("💰 Цена", callback_data="priority_price"),
            InlineKeyboardButton("⚡ Скорость", callback_data="priority_speed"),
            InlineKeyboardButton("⭐ Комфорт", callback_data="priority_comfort")
        ],
        [
            InlineKeyboardButton("🛋️ Удобство", callback_data="priority_convenience"),
            InlineKeyboardButton("⚖️ Баланс", callback_data="priority_balance"),
            InlineKeyboardButton("◀️ Назад", callback_data="settings_back")
        ]
    ]
    return InlineKeyboardMarkup(buttons)

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

def get_settings_keyboard(user_id):
    prefs = get_user_preferences(user_id)
    priority = prefs.get('priority', 'balance')
    max_stops = prefs.get('max_stops', 3)
    pref_hours = prefs.get('preferred_hours', 'all')
    favorite_city = prefs.get('favorite_city', '')
    favorite_airport = prefs.get('favorite_airport', '')
    
    priority_names = {
        'price': '💰 Цена',
        'speed': '⚡ Скорость',
        'comfort': '⭐ Комфорт',
        'convenience': '🛋️ Удобство',
        'balance': '⚖️ Баланс'
    }
    
    stops_names = {
        0: '🟢 Прямые',
        1: '🟡 1 пересадка',
        2: '🟠 2 пересадки',
        3: '🔵 Любые'
    }
    
    hours_names = {
        'morning': '🌅 Утро (6-12)',
        'day': '☀️ День (12-18)',
        'evening': '🌆 Вечер (18-23)',
        'night': '🌙 Ночь (23-6)',
        'all': '🕐 Любое время'
    }
    
    fav_city_name = ""
    if favorite_city:
        for name, code in CITIES.items():
            if code == favorite_city:
                fav_city_name = name
                break
    
    fav_airport_name = get_airport_name(favorite_airport) if favorite_airport else "Не выбран"
    
    buttons = [
        [InlineKeyboardButton(f"🎯 {priority_names.get(priority, 'Баланс')}", callback_data="settings_priority")],
        [InlineKeyboardButton(f"🔄 {stops_names.get(max_stops, 'Любые')}", callback_data="settings_stops")],
        [InlineKeyboardButton(f"⏰ {hours_names.get(pref_hours, 'Любое')}", callback_data="settings_hours")],
        [InlineKeyboardButton(f"⭐ Город: {fav_city_name if fav_city_name else 'Не выбран'}", callback_data="settings_favorite_city")],
        [InlineKeyboardButton(f"🛫 Аэропорт: {fav_airport_name}", callback_data="settings_favorite_airport")],
        [InlineKeyboardButton("🔄 Сбросить настройки", callback_data="reset_settings")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_stops_keyboard():
    buttons = [
        [InlineKeyboardButton("🟢 Прямые (0)", callback_data="stops_0")],
        [InlineKeyboardButton("🟡 1 пересадка", callback_data="stops_1")],
        [InlineKeyboardButton("🟠 2 пересадки", callback_data="stops_2")],
        [InlineKeyboardButton("🔵 Любые", callback_data="stops_3")],
        [InlineKeyboardButton("◀️ Назад", callback_data="settings_back")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_hours_keyboard():
    buttons = [
        [InlineKeyboardButton("🌅 Утро (6-12)", callback_data="hours_morning")],
        [InlineKeyboardButton("☀️ День (12-18)", callback_data="hours_day")],
        [InlineKeyboardButton("🌆 Вечер (18-23)", callback_data="hours_evening")],
        [InlineKeyboardButton("🌙 Ночь (23-6)", callback_data="hours_night")],
        [InlineKeyboardButton("🕐 Любое время", callback_data="hours_all")],
        [InlineKeyboardButton("◀️ Назад", callback_data="settings_back")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_favorite_city_keyboard():
    buttons = []
    row = []
    for i, (name, code) in enumerate(CITIES.items()):
        row.append(InlineKeyboardButton(f"{name} ({code})", callback_data=f"fav_city_{code}"))
        if (i + 1) % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("✏️ Ввести город вручную", callback_data="fav_city_manual")])
    buttons.append([InlineKeyboardButton("❌ Отключить", callback_data="fav_city_none")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="settings_back")])
    return InlineKeyboardMarkup(buttons)

def get_favorite_airport_keyboard(user_id):
    prefs = get_user_preferences(user_id)
    favorite_city = prefs.get('favorite_city', '')
    if not favorite_city:
        buttons = [
            [InlineKeyboardButton("❌ Сначала выберите город", callback_data="settings_back")],
            [InlineKeyboardButton("◀️ Назад", callback_data="settings_back")]
        ]
        return InlineKeyboardMarkup(buttons)
    
    codes = find_city_code(favorite_city)
    buttons = []
    for code in codes:
        airport_name = get_airport_name(code)
        buttons.append([InlineKeyboardButton(f"✈️ {airport_name} ({code})", callback_data=f"fav_airport_{code}")])
    buttons.append([InlineKeyboardButton("❌ Отключить", callback_data="fav_airport_none")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="settings_back")])
    return InlineKeyboardMarkup(buttons)

def get_popular_routes(user_id=None):
    routes = [
        ("IST", "DXB"),
        ("IST", "PEK"),
        ("IST", "BKK"),
        ("DXB", "BKK"),
        ("PEK", "BKK"),
        ("AYT", "IST"),
    ]
    
    if user_id:
        prefs = get_user_preferences(user_id)
        favorite = prefs.get('favorite_city', '')
        if favorite:
            routes = [(favorite, "DXB"), (favorite, "IST"), (favorite, "BKK")] + routes
    
    buttons = []
    for from_city, to_city in routes:
        from_name = from_city
        to_name = to_city
        for name, code in CITIES.items():
            if code == from_city:
                from_name = name
            if code == to_city:
                to_name = name
        buttons.append([InlineKeyboardButton(
            f"✈️ {from_name} → {to_name} ({from_city}→{to_city})",
            callback_data=f"route_{from_city}_{to_city}"
        )])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(buttons)

def get_history_keyboard(user_id):
    history = get_search_history(user_id, limit=10)
    buttons = []
    
    if not history:
        buttons.append([InlineKeyboardButton("📭 История пуста", callback_data="history_empty")])
    else:
        for record in history:
            hist_id, from_city, to_city, date, query_text, created_at = record
            try:
                created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S.%f")
                date_str = created.strftime("%d.%m %H:%M")
            except:
                date_str = "недавно"
            button_text = f"✈️ {from_city} → {to_city}  {date}  ({date_str})"
            callback_data = f"history_{hist_id}_{from_city}_{to_city}_{date}"
            buttons.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    buttons.append([InlineKeyboardButton("🗑️ Очистить историю", callback_data="history_clear")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(buttons)

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    init_db()
    user_id = update.effective_user.id
    get_user_preferences(user_id)
    await update.message.reply_text(
        "✈️ *Добро пожаловать в бот поиска авиабилетов!*\n\n"
        "Я помогу найти лучшие цены на билеты по всему миру.\n"
        "Я проанализирую все варианты и предложу:\n"
        "⭐ *Лучший вариант* (баланс цены и комфорта)\n"
        "💰 *Самый дешевый*\n"
        "⚡ *Самый быстрый*\n\n"
        "Нажмите *«Начать поиск»*, чтобы начать.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_data = context.user_data
    user_id = update.effective_user.id
    
    if text == "✈️ Начать поиск":
        user_data.clear()
        await update.message.reply_text(
            "🌍 *Откуда вылетаем?*\n\n"
            "Выберите город из списка или введите название (на русском или английском):\n"
            "Например: *Стамбул*, *Dubai*, *Пекин*",
            parse_mode="Markdown",
            reply_markup=get_city_keyboard(user_id)
        )
        user_data['state'] = 'from_city'
    
    elif text == "⚙️ Настройки":
        prefs = get_user_preferences(user_id)
        priority = prefs.get('priority', 'balance')
        max_stops = prefs.get('max_stops', 3)
        pref_hours = prefs.get('preferred_hours', 'all')
        favorite_city = prefs.get('favorite_city', '')
        favorite_airport = prefs.get('favorite_airport', '')
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'convenience': '🛋️ Удобство',
            'balance': '⚖️ Баланс'
        }
        stops_names = {
            0: '🟢 Прямые',
            1: '🟡 1 пересадка',
            2: '🟠 2 пересадки',
            3: '🔵 Любые'
        }
        hours_names = {
            'morning': '🌅 Утро (6-12)',
            'day': '☀️ День (12-18)',
            'evening': '🌆 Вечер (18-23)',
            'night': '🌙 Ночь (23-6)',
            'all': '🕐 Любое время'
        }
        
        fav_city_name = ""
        if favorite_city:
            for name, code in CITIES.items():
                if code == favorite_city:
                    fav_city_name = name
                    break
        
        fav_airport_name = get_airport_name(favorite_airport) if favorite_airport else "Не выбран"
        
        await update.message.reply_text(
            f"⚙️ *Ваши настройки:*\n\n"
            f"🎯 Приоритет: {priority_names.get(priority, 'Баланс')}\n"
            f"🔄 Пересадки: {stops_names.get(max_stops, 'Любые')}\n"
            f"⏰ Время: {hours_names.get(pref_hours, 'Любое')}\n"
            f"⭐ Город: {fav_city_name if fav_city_name else 'Не выбран'}\n"
            f"🛫 Аэропорт: {fav_airport_name}\n\n"
            "Нажмите на параметр, чтобы изменить:",
            parse_mode="Markdown",
            reply_markup=get_settings_keyboard(user_id)
        )
    
    elif text == "📊 История":
        await update.message.reply_text(
            "📊 *Ваша история поиска:*\n\n"
            "Нажмите на запрос, чтобы повторить поиск.",
            parse_mode="Markdown",
            reply_markup=get_history_keyboard(user_id)
        )
    
    elif text == "❓ Помощь":
        help_text = (
            "✈️ *Как пользоваться ботом:*\n\n"
            "1️⃣ Нажмите *«Начать поиск»*\n"
            "2️⃣ Выберите город вылета (можно ввести название на русском или английском)\n"
            "3️⃣ Выберите город прибытия\n"
            "4️⃣ Выберите дату\n"
            "5️⃣ Получите 3 варианта!\n\n"
            "*Или отправьте запрос вручную:*\n"
            "`IST → DXB 2026-07-20`\n"
            "`Стамбул → Дубай 2026-07-20`"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    
    elif user_data.get('state') == 'search_by_city':
        codes = find_city_code(text)
        if codes:
            if user_data.get('city_type') == 'from':
                user_data['from_city_codes'] = codes
                user_data['from_city_name'] = text.strip()
                user_data['state'] = 'to_city'
                airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                await update.message.reply_text(
                    f"✅ Найден город: *{text}*\n"
                    f"✈️ Аэропорты: {airports_list}\n\n"
                    "🔍 Буду искать рейсы из всех аэропортов!\n\n"
                    "🌍 *Куда летим?*\nВведите город прибытия:",
                    parse_mode="Markdown"
                )
            else:
                user_data['to_city_codes'] = codes
                user_data['to_city_name'] = text.strip()
                user_data['state'] = 'date'
                airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                await update.message.reply_text(
                    f"✅ Найден город: *{text}*\n"
                    f"✈️ Аэропорты: {airports_list}\n\n"
                    "🔍 Буду искать рейсы во все аэропорты!\n\n"
                    "📅 *Когда летим?*\nВыберите дату:",
                    parse_mode="Markdown",
                    reply_markup=get_date_keyboard()
                )
        else:
            await update.message.reply_text(
                f"❌ Город *{text}* не найден.\n\n"
                "Попробуйте:\n"
                "• Написать на русском (например, Стамбул)\n"
                "• Написать на английском (например, Istanbul)",
                parse_mode="Markdown"
            )
        return
    
    elif user_data.get('state') == 'fav_city_manual':
        codes = find_city_code(text)
        if codes:
            prefs = get_user_preferences(user_id)
            city_code = codes[0]
            prefs['favorite_city'] = city_code
            save_user_preferences(user_id, prefs)
            await update.message.reply_text(
                f"✅ Избранный город: *{text}* ({city_code})",
                parse_mode="Markdown"
            )
            await update.message.reply_text(
                "👇 Выберите действие:",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text(
                f"❌ Город *{text}* не найден.\n\n"
                "Попробуйте на русском или английском.",
                parse_mode="Markdown"
            )
        return
    
    elif user_data.get('state') == 'manual_date':
        if re.match(r'\d{4}-\d{2}-\d{2}', text):
            user_data['date'] = text
            await update.message.reply_text(f"✅ Выбрана дата: *{text}*", parse_mode="Markdown")
            await perform_search(update, context)
        else:
            await update.message.reply_text("❌ Неправильный формат. Используй: ГГГГ-ММ-ДД")
    
    else:
        if len(text) > 3:
            codes = find_city_code(text)
            if codes:
                if user_data.get('state') == 'from_city' or not user_data.get('from_city_codes'):
                    user_data['from_city_codes'] = codes
                    user_data['from_city_name'] = text.strip()
                    user_data['state'] = 'to_city'
                    airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                    await update.message.reply_text(
                        f"✅ Найден город: *{text}*\n"
                        f"✈️ Аэропорты: {airports_list}\n\n"
                        "🔍 Буду искать рейсы из всех аэропортов!\n\n"
                        "🌍 *Куда летим?*\nВведите город прибытия:",
                        parse_mode="Markdown"
                    )
                    return
                else:
                    user_data['to_city_codes'] = codes
                    user_data['to_city_name'] = text.strip()
                    user_data['state'] = 'date'
                    airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                    await update.message.reply_text(
                        f"✅ Найден город: *{text}*\n"
                        f"✈️ Аэропорты: {airports_list}\n\n"
                        "🔍 Буду искать рейсы во все аэропорты!\n\n"
                        "📅 *Когда летим?*\nВыберите дату:",
                        parse_mode="Markdown",
                        reply_markup=get_date_keyboard()
                    )
                    return
        
        await handle_manual_search(update, text, context)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_data = context.user_data
    user_id = update.effective_user.id
    
    logger.info(f"🔘 Callback: {data} от user {user_id}")
    
    if data == "settings_priority":
        await query.edit_message_text(
            "🎯 *Выберите приоритет поиска:*\n\n"
            "💰 *Цена* — самые дешёвые билеты (могут быть с долгими пересадками)\n"
            "⚡ *Скорость* — самые быстрые перелеты (минимальное общее время)\n"
            "⭐ *Комфорт* — минимальное число пересадок (но может быть дороже)\n"
            "🛋️ *Удобство* — короткие пересадки + удобное время вылета\n"
            "⚖️ *Баланс* — оптимальное сочетание цены, времени и комфорта\n\n"
            "💡 *Рекомендация:* если хотите удобную пересадку, выберите 🛋️ Удобство или ⭐ Комфорт",
            parse_mode="Markdown",
            reply_markup=get_priority_keyboard()
        )
        return
    
    # --- ПОИСК ПО ГОРОДУ ---
    if data == "search_by_city":
        user_data['state'] = 'search_by_city'
        if not user_data.get('from_city_codes'):
            user_data['city_type'] = 'from'
            await query.edit_message_text(
                "🔍 *Введите название города вылета*\n\n"
                "Например: *Стамбул*, *Dubai*, *Пекин*\n\n"
                "Я найду все аэропорты автоматически.",
                parse_mode="Markdown"
            )
        else:
            user_data['city_type'] = 'to'
            await query.edit_message_text(
                "🔍 *Введите название города прибытия*\n\n"
                "Например: *Стамбул*, *Dubai*, *Пекин*\n\n"
                "Я найду все аэропорты автоматически.",
                parse_mode="Markdown"
            )
        return
    
    # --- ИЗБРАННЫЙ ГОРОД ---
    elif data == "settings_favorite_city":
        await query.edit_message_text(
            "⭐ *Выберите избранный город вылета*\n\n"
            "Выберите из списка или нажмите «Ввести город вручную»:",
            parse_mode="Markdown",
            reply_markup=get_favorite_city_keyboard()
        )
        return
    
    elif data == "fav_city_manual":
        user_data['state'] = 'fav_city_manual'
        await query.edit_message_text(
            "✏️ *Введите название города*\n\n"
            "Например: *Стамбул*, *Dubai*, *Пекин*\n\n"
            "Бот сам найдёт IATA-код.",
            parse_mode="Markdown"
        )
        return
    
    elif data == "settings_favorite_airport":
        await query.edit_message_text(
            "🛫 *Избранный аэропорт*\n\n"
            "Рейсы из этого аэропорта будут показываться **первыми** в результатах поиска.\n\n"
            "Это НЕ ограничивает поиск — бот всё равно ищет рейсы из всех аэропортов города,\n"
            "но рейсы из избранного аэропорта будут вверху списка.",
            parse_mode="Markdown",
            reply_markup=get_favorite_airport_keyboard(user_id)
        )
        return
    
    elif data.startswith("fav_city_"):
        code = data.replace("fav_city_", "")
        if code == "none":
            prefs = get_user_preferences(user_id)
            prefs['favorite_city'] = ''
            prefs['favorite_airport'] = ''
            save_user_preferences(user_id, prefs)
            await query.edit_message_text("✅ Избранный город *отключен*", parse_mode="Markdown")
        else:
            fav_name = None
            for name, c in CITIES.items():
                if c == code:
                    fav_name = name
                    break
            prefs = get_user_preferences(user_id)
            prefs['favorite_city'] = code
            save_user_preferences(user_id, prefs)
            await query.edit_message_text(
                f"✅ Избранный город: *{fav_name if fav_name else code}* ({code})",
                parse_mode="Markdown"
            )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    elif data.startswith("fav_airport_"):
        code = data.replace("fav_airport_", "")
        if code == "none":
            prefs = get_user_preferences(user_id)
            prefs['favorite_airport'] = ''
            save_user_preferences(user_id, prefs)
            await query.edit_message_text("✅ Избранный аэропорт *отключен*", parse_mode="Markdown")
        else:
            prefs = get_user_preferences(user_id)
            prefs['favorite_airport'] = code
            save_user_preferences(user_id, prefs)
            await query.edit_message_text(
                f"✅ Избранный аэропорт: *{get_airport_name(code)} ({code})*",
                parse_mode="Markdown"
            )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    # --- НАСТРОЙКИ ---
    elif data == "settings_priority":
        await query.edit_message_text(
            "🎯 *Выберите приоритет поиска:*\n\n"
            "💰 *Цена* — самые дешёвые билеты (могут быть с долгими пересадками)\n"
            "⚡ *Скорость* — самые быстрые перелеты (минимальное общее время)\n"
            "⭐ *Комфорт* — минимальное число пересадок (но может быть дороже)\n"
            "🛋️ *Удобство* — короткие пересадки + удобное время вылета\n"
            "⚖️ *Баланс* — оптимальное сочетание цены, времени и комфорта\n\n"
            "💡 *Рекомендация:* если хотите удобную пересадку, выберите 🛋️ Удобство или ⭐ Комфорт",
            parse_mode="Markdown",
            reply_markup=get_priority_keyboard()
        )
        return
    
    elif data == "settings_stops":
        await query.edit_message_text(
            "🔄 *Максимум пересадок:*\n\n"
            "Выберите допустимое количество пересадок:",
            parse_mode="Markdown",
            reply_markup=get_stops_keyboard()
        )
        return
    
    elif data == "settings_hours":
        await query.edit_message_text(
            "⏰ *Удобное время вылета:*\n\n"
            "Выберите предпочтительное время:",
            parse_mode="Markdown",
            reply_markup=get_hours_keyboard()
        )
        return
    
    elif data == "settings_back":
        prefs = get_user_preferences(user_id)
        priority = prefs.get('priority', 'balance')
        max_stops = prefs.get('max_stops', 3)
        pref_hours = prefs.get('preferred_hours', 'all')
        favorite_city = prefs.get('favorite_city', '')
        favorite_airport = prefs.get('favorite_airport', '')
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'convenience': '🛋️ Удобство',
            'balance': '⚖️ Баланс'
        }
        stops_names = {
            0: '🟢 Прямые',
            1: '🟡 1 пересадка',
            2: '🟠 2 пересадки',
            3: '🔵 Любые'
        }
        hours_names = {
            'morning': '🌅 Утро (6-12)',
            'day': '☀️ День (12-18)',
            'evening': '🌆 Вечер (18-23)',
            'night': '🌙 Ночь (23-6)',
            'all': '🕐 Любое время'
        }
        
        fav_city_name = ""
        if favorite_city:
            for name, code in CITIES.items():
                if code == favorite_city:
                    fav_city_name = name
                    break
        fav_airport_name = get_airport_name(favorite_airport) if favorite_airport else "Не выбран"
        
        await query.edit_message_text(
            f"⚙️ *Ваши настройки:*\n\n"
            f"🎯 Приоритет: {priority_names.get(priority, 'Баланс')}\n"
            f"🔄 Пересадки: {stops_names.get(max_stops, 'Любые')}\n"
            f"⏰ Время: {hours_names.get(pref_hours, 'Любое')}\n"
            f"⭐ Город: {fav_city_name if fav_city_name else 'Не выбран'}\n"
            f"🛫 Аэропорт: {fav_airport_name}\n\n"
            "Нажмите на параметр, чтобы изменить:",
            parse_mode="Markdown",
            reply_markup=get_settings_keyboard(user_id)
        )
        return
    
    # --- ПРИОРИТЕТ ---
    elif data.startswith("priority_"):
        priority = data.replace("priority_", "")
        prefs = get_user_preferences(user_id)
        prefs['priority'] = priority
        save_user_preferences(user_id, prefs)
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'convenience': '🛋️ Удобство',
            'balance': '⚖️ Баланс'
        }
        await query.edit_message_text(
            f"✅ Приоритет изменен на: *{priority_names.get(priority, priority)}*\n\n"
            "⚙️ Настройки обновлены!",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    # --- ПЕРЕСАДКИ ---
    elif data.startswith("stops_"):
        stops = int(data.replace("stops_", ""))
        prefs = get_user_preferences(user_id)
        prefs['max_stops'] = stops
        save_user_preferences(user_id, prefs)
        
        stops_names = {
            0: '🟢 Прямые',
            1: '🟡 1 пересадка',
            2: '🟠 2 пересадки',
            3: '🔵 Любые'
        }
        await query.edit_message_text(
            f"✅ Максимум пересадок: *{stops_names.get(stops, 'Любые')}*\n\n"
            "⚙️ Настройки обновлены!",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    # --- ВРЕМЯ ---
    elif data.startswith("hours_"):
        hours = data.replace("hours_", "")
        prefs = get_user_preferences(user_id)
        prefs['preferred_hours'] = hours
        save_user_preferences(user_id, prefs)
        
        hours_names = {
            'morning': '🌅 Утро (6-12)',
            'day': '☀️ День (12-18)',
            'evening': '🌆 Вечер (18-23)',
            'night': '🌙 Ночь (23-6)',
            'all': '🕐 Любое время'
        }
        await query.edit_message_text(
            f"✅ Время вылета: *{hours_names.get(hours, 'Любое')}*\n\n"
            "⚙️ Настройки обновлены!",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    elif data == "reset_settings":
        save_user_preferences(user_id, {'priority': 'balance', 'max_stops': 3, 'preferred_hours': 'all', 'favorite_city': '', 'favorite_airport': '', 'avoid_airports': ''})
        await query.edit_message_text(
            "✅ *Настройки сброшены до стандартных*\n\n"
            "🎯 Приоритет: Баланс\n"
            "🔄 Пересадки: Любые\n"
            "⏰ Время: Любое\n"
            "⭐ Город: Не выбран\n"
            "🛫 Аэропорт: Не выбран",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    # --- ОСТАЛЬНЫЕ CALLBACK-И ---
    elif data == "back_to_main":
        await query.edit_message_text("✈️ *Главное меню*", parse_mode="Markdown")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    elif data == "manual_date":
        user_data['state'] = 'manual_date'
        await query.edit_message_text(
            "✏️ Введите дату в формате: *ГГГГ-ММ-ДД*\n"
            "Например: *2026-07-20*",
            parse_mode="Markdown"
        )
        return
    
    elif data == "popular_routes":
        await query.edit_message_text(
            "✈️ *Популярные маршруты*\n\n"
            "Выберите маршрут для быстрого поиска:",
            parse_mode="Markdown",
            reply_markup=get_popular_routes(user_id)
        )
        return
    
    elif data == "history_empty":
        await query.edit_message_text("📭 История пока пуста. Сделайте свой первый поиск!")
        return
    
    elif data == "history_clear":
        delete_search_history(user_id)
        await query.edit_message_text("🗑️ История успешно очищена!")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    elif data.startswith("history_"):
        parts = data.split("_")
        if len(parts) >= 5:
            hist_id = parts[1]
            from_city = parts[2]
            to_city = parts[3]
            date = parts[4]
            
            logger.info(f"🔄 Повтор поиска из истории: {from_city} → {to_city} {date}")
            user_data['from_city_codes'] = [from_city]
            user_data['to_city_codes'] = [to_city]
            user_data['date'] = date
            
            await query.edit_message_text(f"🔍 Повторяем поиск: {from_city} → {to_city} на {date}")
            await perform_search(update, context)
        return
    
    elif data.startswith("route_"):
        _, from_city, to_city = data.split("_")
        user_data['from_city_codes'] = [from_city]
        user_data['to_city_codes'] = [to_city]
        user_data['state'] = 'date'
        await query.edit_message_text(
            f"✅ Выбран маршрут: *{from_city} → {to_city}*\n\n"
            "📅 *Когда летим?*\nВыберите дату:",
            parse_mode="Markdown",
            reply_markup=get_date_keyboard()
        )
        return
    
    elif data.startswith("city_"):
        code = data.replace("city_", "")
        codes = find_city_code(code)
        if codes:
            if not user_data.get('from_city_codes'):
                user_data['from_city_codes'] = codes
                user_data['from_city_name'] = code
                user_data['state'] = 'to_city'
                airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                await query.edit_message_text(
                    f"✅ Найден город: *{code}*\n"
                    f"✈️ Аэропорты: {airports_list}\n\n"
                    "🔍 Буду искать рейсы из всех аэропортов!\n\n"
                    "🌍 *Куда летим?*\nВыберите город или введите название:",
                    parse_mode="Markdown",
                    reply_markup=get_city_keyboard(user_id)
                )
            else:
                user_data['to_city_codes'] = codes
                user_data['to_city_name'] = code
                user_data['state'] = 'date'
                airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                await query.edit_message_text(
                    f"✅ Найден город: *{code}*\n"
                    f"✈️ Аэропорты: {airports_list}\n\n"
                    "🔍 Буду искать рейсы во все аэропорты!\n\n"
                    "📅 *Когда летим?*\nВыберите дату:",
                    parse_mode="Markdown",
                    reply_markup=get_date_keyboard()
                )
        return
    
    elif data.startswith("date_"):
        date = data.replace("date_", "")
        user_data['date'] = date
        await query.edit_message_text(f"✅ Выбрана дата: *{date}*", parse_mode="Markdown")
        await perform_search(update, context)
        return

async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    user_id = update.effective_user.id
    
    from_codes = user_data.get('from_city_codes', [])
    if not from_codes:
        from_codes = [user_data.get('from_city', '')]
    
    to_codes = user_data.get('to_city_codes', [])
    if not to_codes:
        to_codes = [user_data.get('to_city', '')]
    
    date = user_data.get('date')
    
    logger.info(f"🔍 Поиск: {from_codes} → {to_codes} {date} (user {user_id})")
    logger.info(f"📡 Коды вылета: {from_codes}")
    logger.info(f"📡 Коды прилёта: {to_codes}")
    
    if not from_codes or not to_codes or not date:
        await update.callback_query.edit_message_text("❌ Не все данные введены. Начните заново.")
        user_data.clear()
        return
    
    from_codes = [c for c in from_codes if c]
    to_codes = [c for c in to_codes if c]
    
    if not from_codes or not to_codes:
        await update.callback_query.edit_message_text("❌ Не удалось определить аэропорты. Попробуйте выбрать город из списка.")
        user_data.clear()
        return
    
    try:
        await update.callback_query.edit_message_text("🔍 Ищу билеты... Это займет несколько секунд.")
        
        all_flights = []
        google_flights_count = 0
        aviasales_count = 0
        
        # 1. Google Flights (fast-flights)
        for from_city in from_codes:
            for to_city in to_codes:
                try:
                    q = create_query(
                        flights=[FlightQuery(date=date, from_airport=from_city, to_airport=to_city)],
                        seat="economy",
                        trip="one-way",
                        passengers=Passengers(adults=1),
                        language="en-US",
                    )
                    result = get_flights(q)  # Убрали deep_search=True
                    if result and len(result) > 0:
                        flights_data = parse_flight_data(result)
                        all_flights.extend(flights_data)
                        google_flights_count += len(flights_data)
                        logger.info(f"✅ Google Flights: найдены рейсы {from_city}→{to_city}: {len(flights_data)} шт.")
                except Exception as e:
                    logger.error(f"❌ Ошибка Google Flights {from_city}→{to_city}: {e}")
        
        # 2. Aviasales (GraphQL)
        for from_city in from_codes:
            for to_city in to_codes:
                try:
                    avia_data = search_aviasales(from_city, to_city, date)
                    if avia_data:
                        avia_flights = parse_aviasales_result(avia_data, from_city, to_city, date)
                        all_flights.extend(avia_flights)
                        aviasales_count += len(avia_flights)
                        logger.info(f"✅ Aviasales: найдены рейсы {from_city}→{to_city}: {len(avia_flights)} шт.")
                except Exception as e:
                    logger.error(f"❌ Ошибка Aviasales {from_city}→{to_city}: {e}")
        
        logger.info(f"📊 ИТОГО: Google Flights: {google_flights_count}, Aviasales: {aviasales_count}, Всего: {len(all_flights)}")
        
        if not all_flights:
            await update.callback_query.edit_message_text(
                f"❌ Рейсы не найдены для выбранных направлений.\n\n"
                f"📊 Google Flights: {google_flights_count} рейсов\n"
                f"📊 Aviasales: {aviasales_count} рейсов\n\n"
                "Попробуйте:\n"
                "• Другую дату\n"
                "• Другой город\n"
                "• Проверить написание города"
            )
            user_data.clear()
            return
        
        prefs = get_user_preferences(user_id)
        favorite_airport = prefs.get('favorite_airport', '')
        
        sorted_flights = get_sorted_flights(all_flights, prefs, favorite_airport)
        best_overall, cheapest, fastest = get_best_flights(all_flights, prefs)
        
        from_name = user_data.get('from_city_name', from_codes[0])
        to_name = user_data.get('to_city_name', to_codes[0])
        query_text = f"{from_name} → {to_name} {date}"
        save_search_history(user_id, from_name, to_name, date, query_text, all_flights[:5])
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'convenience': '🛋️ Удобство',
            'balance': '⚖️ Баланс'
        }
        current_priority = prefs.get('priority', 'balance')
        
        response = f"✈️ *Результаты поиска*\n"
        response += f"📍 {from_name} → {to_name}\n"
        response += f"📅 {date}\n"
        response += f"🎯 Приоритет: {priority_names.get(current_priority, 'Баланс')}\n"
        if favorite_airport:
            response += f"🛫 Приоритетный аэропорт: {get_airport_name(favorite_airport)} ({favorite_airport})\n"
        response += f"\n📋 *Найдено {len(sorted_flights)} вариантов:*\n\n"
        
        # Показываем ВСЕ рейсы
        for i, flight in enumerate(sorted_flights, 1):
            response += format_flight_card_compact(flight, index=i) + "\n\n"
        
        # Рекомендованный вариант
        if best_overall:
            response += "⭐ *Рекомендованный вариант:*\n"
            response += format_flight_card_compact(best_overall) + "\n"
            response += f"📌 *Почему:* {get_reason_compact(best_overall, prefs)}\n\n"
        
        response += "💡 Для покупки перейдите на сайт авиакомпании."
        
        await update.callback_query.edit_message_text(response, parse_mode="Markdown")
        user_data.clear()
        await update.callback_query.message.reply_text(
            "✈️ Поиск завершен! Нажмите *«Начать поиск»* для нового поиска.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}")
        await update.callback_query.edit_message_text(f"❌ Ошибка: {str(e)}")
        user_data.clear()

async def handle_manual_search(update: Update, text, context):
    try:
        parts = text.split("→")
        if len(parts) != 2:
            await update.message.reply_text("❌ Используй формат: IST → DXB 2026-07-20")
            context.user_data.clear()
            return
        
        from_city = parts[0].strip().upper()
        rest = parts[1].strip().split(" ")
        if len(rest) < 2:
            await update.message.reply_text("❌ Не указана дата.")
            context.user_data.clear()
            return
        
        to_city = rest[0].strip().upper()
        date = rest[1].strip()
        
        if not re.match(r'\d{4}-\d{2}-\d{2}', date):
            await update.message.reply_text("❌ Неправильный формат даты. Используй ГГГГ-ММ-ДД")
            context.user_data.clear()
            return
        
        from_codes = find_city_code(from_city)
        if not from_codes:
            from_codes = [from_city]
        
        to_codes = find_city_code(to_city)
        if not to_codes:
            to_codes = [to_city]
        
        context.user_data['from_city_codes'] = from_codes
        context.user_data['to_city_codes'] = to_codes
        context.user_data['from_city_name'] = from_city
        context.user_data['to_city_name'] = to_city
        context.user_data['date'] = date
        
        user_id = update.effective_user.id
        await update.message.reply_text("🔍 Ищу билеты... Это займет несколько секунд.")
        
        all_flights = []
        google_flights_count = 0
        aviasales_count = 0
        
        # Google Flights
        for from_c in from_codes:
            for to_c in to_codes:
                try:
                    q = create_query(
                        flights=[FlightQuery(date=date, from_airport=from_c, to_airport=to_c)],
                        seat="economy",
                        trip="one-way",
                        passengers=Passengers(adults=1),
                        language="en-US",
                    )
                    result = get_flights(q)  # Убрали deep_search=True
                    if result and len(result) > 0:
                        flights_data = parse_flight_data(result)
                        all_flights.extend(flights_data)
                        google_flights_count += len(flights_data)
                except Exception as e:
                    logger.error(f"Ошибка Google Flights {from_c}→{to_c}: {e}")
        
        # Aviasales
        for from_c in from_codes:
            for to_c in to_codes:
                try:
                    avia_data = search_aviasales(from_c, to_c, date)
                    if avia_data:
                        avia_flights = parse_aviasales_result(avia_data, from_c, to_c, date)
                        all_flights.extend(avia_flights)
                        aviasales_count += len(avia_flights)
                except Exception as e:
                    logger.error(f"Ошибка Aviasales {from_c}→{to_c}: {e}")
        
        logger.info(f"📊 ИТОГО: Google Flights: {google_flights_count}, Aviasales: {aviasales_count}, Всего: {len(all_flights)}")
        
        if not all_flights:
            await update.message.reply_text(
                f"❌ Рейсы не найдены.\n\n"
                f"📊 Google Flights: {google_flights_count} рейсов\n"
                f"📊 Aviasales: {aviasales_count} рейсов\n\n"
                "Попробуйте другую дату или направление."
            )
            context.user_data.clear()
            return
        
        prefs = get_user_preferences(user_id)
        favorite_airport = prefs.get('favorite_airport', '')
        
        sorted_flights = get_sorted_flights(all_flights, prefs, favorite_airport)
        best_overall, cheapest, fastest = get_best_flights(all_flights, prefs)
        
        query_text = f"{from_city} → {to_city} {date}"
        save_search_history(user_id, from_city, to_city, date, query_text, all_flights[:5])
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'convenience': '🛋️ Удобство',
            'balance': '⚖️ Баланс'
        }
        current_priority = prefs.get('priority', 'balance')
        
        response = f"✈️ *Результаты поиска*\n"
        response += f"📍 {from_city} → {to_city}\n"
        response += f"📅 {date}\n"
        response += f"🎯 Приоритет: {priority_names.get(current_priority, 'Баланс')}\n"
        if favorite_airport:
            response += f"🛫 Приоритетный аэропорт: {get_airport_name(favorite_airport)} ({favorite_airport})\n"
        response += f"\n📋 *Найдено {len(sorted_flights)} вариантов:*\n\n"
        
        for i, flight in enumerate(sorted_flights, 1):
            response += format_flight_card_compact(flight, index=i) + "\n\n"
        
        if best_overall:
            response += "⭐ *Рекомендованный вариант:*\n"
            response += format_flight_card_compact(best_overall) + "\n"
            response += f"📌 *Почему:* {get_reason_compact(best_overall, prefs)}\n\n"
        
        response += "💡 Для покупки перейдите на сайт авиакомпании."
        
        await update.message.reply_text(response, parse_mode="Markdown")
        context.user_data.clear()
        await update.message.reply_text(
            "✈️ Поиск завершен! Нажмите *«Начать поиск»* для нового поиска.",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")
        context.user_data.clear()

# --- ЗАПУСК ---
def run_bot():
    reset_webhook()
    
    while True:
        try:
            print("🚀 Запуск бота...")
            app = Application.builder().token(TOKEN).connect_timeout(60).read_timeout(60).build()
            app.add_handler(CommandHandler("start", start))
            app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            app.add_handler(CallbackQueryHandler(callback_handler))
            print("✅ Бот запущен и готов к работе!")
            app.run_polling()
        except Exception as e:
            print(f"❌ Бот упал с ошибкой: {e}")
            print("🔄 Перезапуск через 5 секунд...")
            time.sleep(5)

if __name__ == "__main__":
    init_db()
    
    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()
    
    ping_thread = threading.Thread(target=keep_alive)
    ping_thread.daemon = True
    ping_thread.start()
    
    run_bot()
