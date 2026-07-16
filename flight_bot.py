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
    cursor.execute('SELECT priority, max_stops, preferred_hours, favorite_city, avoid_airports FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            'priority': row[0],
            'max_stops': row[1],
            'preferred_hours': row[2],
            'favorite_city': row[3] or '',
            'avoid_airports': row[4] or ''
        }
    return {'priority': 'balance', 'max_stops': 3, 'preferred_hours': 'all', 'favorite_city': '', 'avoid_airports': ''}

def save_user_preferences(user_id, preferences):
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, priority, max_stops, preferred_hours, favorite_city, avoid_airports)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (user_id, 
          preferences.get('priority', 'balance'),
          preferences.get('max_stops', 3),
          preferences.get('preferred_hours', 'all'),
          preferences.get('favorite_city', ''),
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

# --- ЗАГРУЗКА БАЗЫ АЭРОПОРТОВ ---
def load_airports():
    """Загружает базу аэропортов из файла airports.dat"""
    city_to_iata = {}
    try:
        with open('airports.dat', 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                # row[2] — город, row[4] — IATA-код
                city = row[2].strip().lower()
                iata = row[4].strip()
                if city and iata:
                    if city not in city_to_iata:
                        city_to_iata[city] = []
                    city_to_iata[city].append(iata)
        logger.info(f"✅ Загружено {len(city_to_iata)} городов с аэропортами")
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки базы аэропортов: {e}")
        city_to_iata = get_fallback_city_dict()
    return city_to_iata

def get_fallback_city_dict():
    """Запасной словарь на случай, если файл не загрузился"""
    return {
        "москва": ["MOW", "SVO", "DME", "VKO"],
        "moscow": ["MOW", "SVO", "DME", "VKO"],
        "лондон": ["LON", "LHR", "LCY", "STN", "LGW"],
        "london": ["LON", "LHR", "LCY", "STN", "LGW"],
        "нью-йорк": ["NYC", "JFK", "EWR", "LGA"],
        "new york": ["NYC", "JFK", "EWR", "LGA"],
        "париж": ["PAR", "CDG", "ORY", "BVA"],
        "paris": ["PAR", "CDG", "ORY", "BVA"],
        "дубай": ["DXB", "DWC"],
        "dubai": ["DXB", "DWC"],
        "стамбул": ["IST", "SAW"],
        "istanbul": ["IST", "SAW"],
    }

# Загружаем базу при старте
CITY_TO_IATA = load_airports()

def find_city_code(city_name):
    """Ищет IATA-код(ы) по названию города"""
    if not city_name:
        return []
    
    city_lower = city_name.lower().strip()
    
    # Проверяем, может это уже IATA-код (3 буквы)
    if len(city_lower) == 3 and city_lower.isupper():
        return [city_lower.upper()]
    
    # Ищем в базе
    if city_lower in CITY_TO_IATA:
        return CITY_TO_IATA[city_lower]
    
    # Частичное совпадение
    results = []
    for city, codes in CITY_TO_IATA.items():
        if city_lower in city or city in city_lower:
            results.extend(codes)
    return list(set(results))

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
MONTHS_RU = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
}

WEEKDAYS_RU = {
    0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'
}

# Основной список городов для кнопок
CITIES = {
    "Лондон": "LHR",
    "Нью-Йорк": "JFK",
    "Париж": "CDG",
    "Дубай": "DXB",
    "Стамбул": "IST",
    "Токио": "NRT",
    "Сингапур": "SIN",
    "Сидней": "SYD",
    "Бангкок": "BKK",
    "Сеул": "ICN",
    "Рим": "FCO",
    "Амстердам": "AMS",
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
    """Компактная карточка рейса"""
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
        card += f"   {seg['from_code']} → {seg['to_code']}  🛫 {dep}  🛬 {arr}  ⏱ {dur}\n"
    
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

def get_sorted_flights(flights_data, user_preferences):
    """Сортирует рейсы в зависимости от приоритета пользователя"""
    if not flights_data:
        return []
    
    max_stops = user_preferences.get('max_stops', 3)
    filtered = [f for f in flights_data if f['stops'] <= max_stops]
    
    if not filtered:
        filtered = flights_data
    
    for flight in filtered:
        flight['score'] = rate_flight(flight, user_preferences)
    
    priority = user_preferences.get('priority', 'balance')
    
    if priority == 'price':
        sorted_flights = sorted(filtered, key=lambda x: x['price_usd'])
    elif priority == 'speed':
        sorted_flights = sorted(filtered, key=lambda x: x['total_duration'])
    elif priority == 'comfort':
        sorted_flights = sorted(filtered, key=lambda x: x['stops'])
    else:  # balance
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
    """Создает клавиатуру выбора города с учетом избранного"""
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
    buttons.append([InlineKeyboardButton("✏️ Ввести IATA-код", callback_data="manual_city")])
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
    favorite = prefs.get('favorite_city', '')
    
    priority_names = {
        'price': '💰 Цена',
        'speed': '⚡ Скорость',
        'comfort': '⭐ Комфорт',
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
    
    fav_name = ""
    if favorite:
        for name, code in CITIES.items():
            if code == favorite:
                fav_name = name
                break
    
    buttons = [
        [InlineKeyboardButton(f"🎯 {priority_names.get(priority, 'Баланс')}", callback_data="settings_priority")],
        [InlineKeyboardButton(f"🔄 {stops_names.get(max_stops, 'Любые')}", callback_data="settings_stops")],
        [InlineKeyboardButton(f"⏰ {hours_names.get(pref_hours, 'Любое')}", callback_data="settings_hours")],
        [InlineKeyboardButton(f"⭐ Избранный: {fav_name if fav_name else 'Не выбран'}", callback_data="settings_favorite")],
        [InlineKeyboardButton("🔄 Сбросить настройки", callback_data="reset_settings")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_priority_keyboard():
    buttons = [
        [InlineKeyboardButton("💰 Цена", callback_data="priority_price")],
        [InlineKeyboardButton("⚡ Скорость", callback_data="priority_speed")],
        [InlineKeyboardButton("⭐ Комфорт", callback_data="priority_comfort")],
        [InlineKeyboardButton("⚖️ Баланс", callback_data="priority_balance")],
        [InlineKeyboardButton("◀️ Назад", callback_data="settings_back")]
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

def get_favorite_keyboard():
    """Клавиатура для выбора избранного города"""
    buttons = []
    row = []
    for i, (name, code) in enumerate(CITIES.items()):
        row.append(InlineKeyboardButton(f"{name} ({code})", callback_data=f"fav_{code}"))
        if (i + 1) % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("❌ Отключить избранный", callback_data="fav_none")])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="settings_back")])
    return InlineKeyboardMarkup(buttons)

def get_popular_routes(user_id=None):
    """Популярные маршруты с учетом избранного города"""
    routes = [
        ("LHR", "JFK"),
        ("CDG", "DXB"),
        ("IST", "LHR"),
        ("SIN", "SYD"),
        ("BKK", "ICN"),
        ("AMS", "FCO"),
    ]
    
    if user_id:
        prefs = get_user_preferences(user_id)
        favorite = prefs.get('favorite_city', '')
        if favorite:
            routes = [(favorite, "JFK"), (favorite, "LHR"), (favorite, "DXB")] + routes
    
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
        await update.message.reply_text(
            "🌍 *Откуда вылетаем?*\n\n"
            "Выберите город из списка, введите название города или IATA-код (3 буквы):\n"
            "Например: *Москва*, *Лондон*, *LHR*, *JFK*",
            parse_mode="Markdown",
            reply_markup=get_city_keyboard(user_id)
        )
        user_data['state'] = 'from_city'
    
    elif text == "⚙️ Настройки":
        prefs = get_user_preferences(user_id)
        priority = prefs.get('priority', 'balance')
        max_stops = prefs.get('max_stops', 3)
        pref_hours = prefs.get('preferred_hours', 'all')
        favorite = prefs.get('favorite_city', '')
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
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
        
        fav_name = ""
        if favorite:
            for name, code in CITIES.items():
                if code == favorite:
                    fav_name = name
                    break
        
        await update.message.reply_text(
            f"⚙️ *Ваши настройки:*\n\n"
            f"🎯 Приоритет: {priority_names.get(priority, 'Баланс')}\n"
            f"🔄 Пересадки: {stops_names.get(max_stops, 'Любые')}\n"
            f"⏰ Время: {hours_names.get(pref_hours, 'Любое')}\n"
            f"⭐ Избранный город: {fav_name if fav_name else 'Не выбран'}\n\n"
            "Нажмите на параметр, чтобы изменить:",
            parse_mode="Markdown",
            reply_markup=get_settings_keyboard(user_id)
        )
    
    elif text == "📊 История":
        logger.info(f"📊 Пользователь {user_id} запросил историю")
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
            "2️⃣ Выберите город вылета (можно ввести название)\n"
            "3️⃣ Выберите город прибытия\n"
            "4️⃣ Выберите дату\n"
            "5️⃣ Получите 3 варианта:\n"
            "   ⭐ Лучший (рекомендованный)\n"
            "   💰 Самый дешевый\n"
            "   ⚡ Самый быстрый\n\n"
            "*Или отправьте запрос вручную:*\n"
            "`LHR → JFK 2026-07-20`\n"
            "`Москва → Стамбул 2026-07-20`"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    
    elif user_data.get('state') == 'manual_city':
        if len(text) == 3 and text.isupper():
            if user_data.get('city_type') == 'from':
                user_data['from_city'] = text
                user_data['state'] = 'to_city'
                await update.message.reply_text(
                    f"✅ Выбран вылет: *{text}*\n\n"
                    "🌍 *Куда летим?*\nВыберите город:",
                    parse_mode="Markdown",
                    reply_markup=get_city_keyboard(user_id)
                )
            else:
                user_data['to_city'] = text
                user_data['state'] = 'date'
                await update.message.reply_text(
                    f"✅ Выбран прилет: *{text}*\n\n"
                    "📅 *Когда летим?*\nВыберите дату:",
                    parse_mode="Markdown",
                    reply_markup=get_date_keyboard()
                )
        else:
            await update.message.reply_text("❌ Введите IATA-код (3 заглавные буквы), например: LHR")
    
    elif user_data.get('state') == 'search_by_city':
        # Пользователь ввел название города
        codes = find_city_code(text)
        if codes:
            # Если несколько кодов — выбираем первый
            code = codes[0]
            if user_data.get('city_type') == 'from':
                user_data['from_city'] = code
                user_data['state'] = 'to_city'
                airports_text = ", ".join(codes)
                await update.message.reply_text(
                    f"✅ Найден город: *{text}* → коды: {airports_text}\n"
                    f"✅ Выбран вылет: *{code}*\n\n"
                    "🌍 *Куда летим?*\nВыберите город или введите название:",
                    parse_mode="Markdown",
                    reply_markup=get_city_keyboard(user_id)
                )
            else:
                user_data['to_city'] = code
                user_data['state'] = 'date'
                airports_text = ", ".join(codes)
                await update.message.reply_text(
                    f"✅ Найден город: *{text}* → коды: {airports_text}\n"
                    f"✅ Выбран прилет: *{code}*\n\n"
                    "📅 *Когда летим?*\nВыберите дату:",
                    parse_mode="Markdown",
                    reply_markup=get_date_keyboard()
                )
        else:
            await update.message.reply_text(
                f"❌ Город *{text}* не найден.\n\n"
                "Попробуйте:\n"
                "• Написать на английском (например, Moscow)\n"
                "• Использовать IATA-код (3 буквы, например LHR)\n"
                "• Выбрать из списка городов",
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
        # Проверяем, может это поиск по городу
        if len(text) > 3 and not text.isupper():
            # Пробуем найти город
            codes = find_city_code(text)
            if codes:
                # Если нашли — предлагаем использовать
                code = codes[0]
                airports_text = ", ".join(codes)
                await update.message.reply_text(
                    f"🔍 Найден город: *{text}* → коды: {airports_text}\n"
                    f"Использовать код *{code}* для поиска?\n\n"
                    f"Отправьте `{code} → ...` или выберите из списка.",
                    parse_mode="Markdown"
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
    
    # --- ПОИСК ПО ГОРОДУ ---
    if data == "search_by_city":
        user_data['state'] = 'search_by_city'
        if not user_data.get('from_city'):
            user_data['city_type'] = 'from'
            await query.edit_message_text(
                "🔍 *Введите название города вылета*\n\n"
                "Например: *Москва*, *London*, *Нью-Йорк*\n\n"
                "Я найду IATA-код автоматически.",
                parse_mode="Markdown"
            )
        else:
            user_data['city_type'] = 'to'
            await query.edit_message_text(
                "🔍 *Введите название города прибытия*\n\n"
                "Например: *Москва*, *London*, *Нью-Йорк*\n\n"
                "Я найду IATA-код автоматически.",
                parse_mode="Markdown"
            )
        return
    
    # --- ИЗБРАННЫЙ ГОРОД ---
    elif data == "settings_favorite":
        await query.edit_message_text(
            "⭐ *Выберите избранный город вылета*\n\n"
            "Этот город будет показываться первым в списке при поиске.\n\n"
            "Выберите город:",
            parse_mode="Markdown",
            reply_markup=get_favorite_keyboard()
        )
        return
    
    elif data.startswith("fav_"):
        code = data.replace("fav_", "")
        if code == "none":
            prefs = get_user_preferences(user_id)
            prefs['favorite_city'] = ''
            save_user_preferences(user_id, prefs)
            await query.edit_message_text(
                "✅ Избранный город *отключен*",
                parse_mode="Markdown"
            )
        else:
            # Находим название города
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
        
        # Возвращаем в настройки
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    # --- НАСТРОЙКИ ---
    elif data == "settings_priority":
        await query.edit_message_text(
            "🎯 *Выберите приоритет поиска:*\n\n"
            "💰 *Цена* — самые дешевые билеты\n"
            "⚡ *Скорость* — самые быстрые перелеты\n"
            "⭐ *Комфорт* — минимальное число пересадок\n"
            "⚖️ *Баланс* — оптимальное сочетание",
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
        favorite = prefs.get('favorite_city', '')
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
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
        
        fav_name = ""
        if favorite:
            for name, code in CITIES.items():
                if code == favorite:
                    fav_name = name
                    break
        
        await query.edit_message_text(
            f"⚙️ *Ваши настройки:*\n\n"
            f"🎯 Приоритет: {priority_names.get(priority, 'Баланс')}\n"
            f"🔄 Пересадки: {stops_names.get(max_stops, 'Любые')}\n"
            f"⏰ Время: {hours_names.get(pref_hours, 'Любое')}\n"
            f"⭐ Избранный город: {fav_name if fav_name else 'Не выбран'}\n\n"
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
        save_user_preferences(user_id, {'priority': 'balance', 'max_stops': 3, 'preferred_hours': 'all', 'favorite_city': '', 'avoid_airports': ''})
        await query.edit_message_text(
            "✅ *Настройки сброшены до стандартных*\n\n"
            "🎯 Приоритет: Баланс\n"
            "🔄 Пересадки: Любые\n"
            "⏰ Время: Любое\n"
            "⭐ Избранный город: Не выбран",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    # --- ОСТАЛЬНЫЕ CALLBACK-И ---
    elif data == "back_to_main":
        await query.edit_message_text("✈️ *Главное меню*", parse_mode="Markdown")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    elif data == "manual_city":
        user_data['state'] = 'manual_city'
        if not user_data.get('from_city'):
            user_data['city_type'] = 'from'
            await query.edit_message_text(
                "✏️ Введите IATA-код *города вылета* (3 буквы):\n"
                "Например: *LHR*, *JFK*, *CDG*",
                parse_mode="Markdown"
            )
        else:
            user_data['city_type'] = 'to'
            await query.edit_message_text(
                "✏️ Введите IATA-код *города прибытия* (3 буквы):\n"
                "Например: *LHR*, *JFK*, *CDG*",
                parse_mode="Markdown"
            )
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
            user_data['from_city'] = from_city
            user_data['to_city'] = to_city
            user_data['date'] = date
            
            await query.edit_message_text(f"🔍 Повторяем поиск: {from_city} → {to_city} на {date}")
            await perform_search(update, context)
        return
    
    elif data.startswith("route_"):
        _, from_city, to_city = data.split("_")
        user_data['from_city'] = from_city
        user_data['to_city'] = to_city
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
        if not user_data.get('from_city'):
            user_data['from_city'] = code
            user_data['state'] = 'to_city'
            await query.edit_message_text(
                f"✅ Выбран вылет: *{code}*\n\n"
                "🌍 *Куда летим?*\nВыберите город:",
                parse_mode="Markdown",
                reply_markup=get_city_keyboard(user_id)
            )
        else:
            user_data['to_city'] = code
            user_data['state'] = 'date'
            await query.edit_message_text(
                f"✅ Выбран прилет: *{code}*\n\n"
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
    from_city = user_data.get('from_city')
    to_city = user_data.get('to_city')
    date = user_data.get('date')
    
    logger.info(f"🔍 Поиск: {from_city} → {to_city} {date} (user {user_id})")
    
    if not from_city or not to_city or not date:
        await update.callback_query.edit_message_text("❌ Не все данные введены. Начните заново.")
        return
    
    try:
        await update.callback_query.edit_message_text("🔍 Ищу билеты... Это займет несколько секунд.")
        
        q = create_query(
            flights=[FlightQuery(date=date, from_airport=from_city, to_airport=to_city)],
            seat="economy",
            trip="one-way",
            passengers=Passengers(adults=1),
            language="en-US",
        )
        result = get_flights(q)
        
        if not result or len(result) == 0:
            await update.callback_query.edit_message_text(f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}.")
            return
        
        flights_data = parse_flight_data(result)
        if not flights_data:
            await update.callback_query.edit_message_text("❌ Не удалось получить детали рейсов.")
            return
        
        prefs = get_user_preferences(user_id)
        
        sorted_flights = get_sorted_flights(flights_data, prefs)
        best_overall, cheapest, fastest = get_best_flights(flights_data, prefs)
        
        query_text = f"{from_city} → {to_city} {date}"
        save_search_history(user_id, from_city, to_city, date, query_text, flights_data[:5])
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'balance': '⚖️ Баланс'
        }
        current_priority = prefs.get('priority', 'balance')
        
        response = f"✈️ *Результаты поиска:*\n"
        response += f"🎯 Приоритет: {priority_names.get(current_priority, 'Баланс')}\n\n"
        
        if sorted_flights:
            response += f"📋 *Топ {min(5, len(sorted_flights))} вариантов:*\n\n"
            for i, flight in enumerate(sorted_flights[:5], 1):
                response += format_flight_card(flight, index=i) + "\n\n"
        
        if best_overall:
            response += "⭐ *Рекомендованный вариант:*\n"
            response += format_flight_card(best_overall) + "\n"
            response += f"📌 *Почему:* {get_reason(best_overall, prefs)}\n\n"
        
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

async def handle_manual_search(update: Update, text, context):
    try:
        parts = text.split("→")
        if len(parts) != 2:
            await update.message.reply_text("❌ Используй формат: LHR → JFK 2026-07-20")
            return
        
        from_city = parts[0].strip().upper()
        rest = parts[1].strip().split(" ")
        if len(rest) < 2:
            await update.message.reply_text("❌ Не указана дата.")
            return
        
        to_city = rest[0].strip().upper()
        date = rest[1].strip()
        
        if not re.match(r'\d{4}-\d{2}-\d{2}', date):
            await update.message.reply_text("❌ Неправильный формат даты. Используй ГГГГ-ММ-ДД")
            return
        
        context.user_data['from_city'] = from_city
        context.user_data['to_city'] = to_city
        context.user_data['date'] = date
        
        user_id = update.effective_user.id
        await update.message.reply_text("🔍 Ищу билеты... Это займет несколько секунд.")
        
        q = create_query(
            flights=[FlightQuery(date=date, from_airport=from_city, to_airport=to_city)],
            seat="economy",
            trip="one-way",
            passengers=Passengers(adults=1),
            language="en-US",
        )
        result = get_flights(q)
        
        if not result or len(result) == 0:
            await update.message.reply_text(f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}.")
            return
        
        flights_data = parse_flight_data(result)
        if not flights_data:
            await update.message.reply_text("❌ Не удалось получить детали рейсов.")
            return
        
        prefs = get_user_preferences(user_id)
        
        sorted_flights = get_sorted_flights(flights_data, prefs)
        best_overall, cheapest, fastest = get_best_flights(flights_data, prefs)
        
        query_text = f"{from_city} → {to_city} {date}"
        save_search_history(user_id, from_city, to_city, date, query_text, flights_data[:5])
        
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'balance': '⚖️ Баланс'
        }
        current_priority = prefs.get('priority', 'balance')
        
        response = f"✈️ *Результаты поиска:*\n"
        response += f"🎯 Приоритет: {priority_names.get(current_priority, 'Баланс')}\n\n"
        
        if sorted_flights:
            response += f"📋 *Топ {min(5, len(sorted_flights))} вариантов:*\n\n"
            for i, flight in enumerate(sorted_flights[:5], 1):
                response += format_flight_card(flight, index=i) + "\n\n"
        
        if best_overall:
            response += "⭐ *Рекомендованный вариант:*\n"
            response += format_flight_card(best_overall) + "\n"
            response += f"📌 *Почему:* {get_reason(best_overall, prefs)}\n\n"
        
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
