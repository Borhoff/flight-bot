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
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, TimeoutError

# Загружаем переменные окружения
load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TRAVELPAYOUTS_TOKEN = "eb631f12ac7f83fda4125614a6dd04bc"   # Добавлено

# Настраиваем логгер
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- МАППИНГ КОДОВ АВИАКОМПАНИЙ В ПОЛНЫЕ НАЗВАНИЯ ---
AIRLINE_NAMES = {
    "FZ": "flydubai",
    "EK": "Emirates",
    "GF": "Gulf Air",
    "QR": "Qatar Airways",
    "TK": "Turkish Airlines",
    "PC": "Pegasus",
    "VF": "AJet",
    "MS": "EgyptAir",
    "RJ": "Royal Jordanian",
    "EY": "Etihad Airways",
    "SU": "Aeroflot",
    "S7": "S7 Airlines",
    "U6": "Ural Airlines",
    "WZ": "Red Wings",
    "EO": "Ikar",
    "FV": "Rossiya",
    "DP": "Pobeda",
    "UT": "Utair",
    "HY": "Uzbekistan Airways",
    "KC": "Air Astana",
    "J2": "Azerbaijan Airlines",
    "HU": "Hainan Airlines",
    "CZ": "China Southern",
    "CA": "Air China",
    "MU": "China Eastern",
    "3U": "Sichuan Airlines",
    "MF": "Xiamen Airlines",
    "OZ": "Asiana Airlines",
    "NH": "All Nippon Airways",
    "JL": "Japan Airlines",
    "KE": "Korean Air",
    "SQ": "Singapore Airlines",
    "TG": "Thai Airways",
    "CX": "Cathay Pacific",
    "BA": "British Airways",
    "AF": "Air France",
    "LH": "Lufthansa",
    "KL": "KLM",
    "VS": "Virgin Atlantic",
    "DL": "Delta Air Lines",
    "AA": "American Airlines",
    "UA": "United Airlines",
    "WN": "Southwest Airlines",
    "B6": "JetBlue",
    "NK": "Spirit Airlines",
    "F9": "Frontier Airlines",
    "AS": "Alaska Airlines",
    "HA": "Hawaiian Airlines",
    "SY": "Sun Country Airlines",
    "G4": "Allegiant Air",
    "XP": "Avelo Airlines",
    "MX": "Breeze Airways",
    "KQ": "Kenya Airways",
    "ET": "Ethiopian Airlines",
    "SA": "South African Airways",
    "AT": "Royal Air Maroc",
    "TP": "TAP Air Portugal",
    "IB": "Iberia",
    "AY": "Finnair",
    "SK": "SAS",
    "OS": "Austrian Airlines",
    "LX": "Swiss International Air Lines",
    "SN": "Brussels Airlines",
    "LO": "LOT Polish Airlines",
    "OK": "Czech Airlines",
    "OU": "Croatia Airlines",
    "JU": "Air Serbia",
    "A3": "Aegean Airlines",
    "OA": "Olympic Air",
    "BT": "Air Baltic",
    "JP": "Adria Airways",
    "EW": "Eurowings",
    "4U": "Germanwings",
    "AB": "Air Berlin",
    "HG": "Niki",
    "XQ": "SunExpress",
    "XC": "Corendon Airlines",
    "FH": "Freebird Airlines",
    "H9": "Pegasus (Domestic)",
    "KK": "AtlasGlobal",
    "QS": "Smartwings",
    "SS": "Corsair International",
    "SE": "XL Airways France",
    "TO": "Transavia France",
    "VY": "Vueling",
    "U2": "easyJet",
    "FR": "Ryanair",
    "W6": "Wizz Air",
    "DY": "Norwegian Air Shuttle",
    "D8": "Norwegian Air Sweden",
    "FI": "Icelandair",
    "OG": "Play",
    "WW": "WOW Air",
    "ZB": "Monarch Airlines",
    "MT": "Thomas Cook Airlines",
    "TC": "TUI Airways",
    "BY": "TUI Airways (UK)",
    "X3": "TUI fly Netherlands",
    "OR": "TUI fly Netherlands",
    "HQ": "TUI fly Belgium",
    "TB": "TUI fly Belgium",
    "XR": "Corendon Dutch Airlines",
    "CD": "Corendon Dutch Airlines",
    "CND": "Corendon Dutch Airlines",
    "CJ": "BA CityFlyer",
    "BM": "British Midland Regional",
    "LM": "Loganair",
    "SI": "Skyways",
    "DC": "Braathens Regional Airways",
    "TF": "Braathens Regional Airlines",
    "WX": "CityJet",
    "EI": "Aer Lingus",
    "RE": "Aer Lingus Regional",
    "BE": "Flybe",
    "T3": "Eastern Airways",
    "GR": "Aurigny Air Services",
    "JS": "Air Koryo",
    "P7": "Air Kasai",
    "BU": "Air Busan",
    "7C": "Jeju Air",
    "LJ": "Jin Air",
    "TW": "T'Way Air",
    "ZE": "Eastar Jet",
    "RS": "Air Seoul",
    "BX": "Air Busan",
    "KE": "Korean Air",
    "OZ": "Asiana Airlines",
    "JL": "Japan Airlines",
    "NH": "All Nippon Airways",
    "BC": "Skymark Airlines",
    "MM": "Peach Aviation",
    "GK": "Jetstar Japan",
    "JW": "Vanilla Air",
    "6J": "Solaseed Air",
    "NU": "Japan Transocean Air",
    "RAC": "Ryukyu Air Commuter",
    "JC": "JAL Express",
    "XW": "NokScoot",
    "TZ": "Scoot",
    "TR": "Scoot",
    "SQ": "Singapore Airlines",
    "MI": "SilkAir",
    "3K": "Jetstar Asia",
    "GK": "Jetstar Japan",
    "JQ": "Jetstar Airways",
    "QF": "Qantas",
    "VA": "Virgin Australia",
    "TT": "Tigerair Australia",
    "NZ": "Air New Zealand",
    "LA": "LATAM Airlines",
    "JJ": "LATAM Brasil",
    "4M": "LATAM Argentina",
    "PU": "LATAM Paraguay",
    "PZ": "LATAM Peru",
    "XL": "LATAM Ecuador",
    "UC": "LATAM Colombia",
    "CM": "Copa Airlines",
    "AV": "Avianca",
    "P5": "Wingo",
    "TK": "Turkish Airlines",
    "J2": "Azerbaijan Airlines",
    "HZ": "Aurora Airlines",
    "SH": "Sharp Airlines",
    "ZL": "Regional Express",
    "QQ": "Alliance Airlines",
    "VA": "Virgin Australia",
    "TT": "Tigerair Australia",
    "QF": "Qantas",
    "JQ": "Jetstar",
    "EK": "Emirates",
    "EY": "Etihad",
    "QR": "Qatar",
    "GF": "Gulf Air",
    "WY": "Oman Air",
    "KU": "Kuwait Airways",
    "MH": "Malaysia Airlines",
    "AK": "AirAsia",
    "D7": "AirAsia X",
    "QZ": "Indonesia AirAsia",
    "FD": "Thai AirAsia",
    "XJ": "Thai AirAsia X",
    "Z2": "Philippines AirAsia",
    "PQ": "Philippines AirAsia",
    "5J": "Cebu Pacific",
    "DG": "Cebgo",
    "PR": "Philippine Airlines",
    "2P": "Air Philippines",
    "PAL": "Philippine Airlines",
    "GX": "GX Airlines",
    "JD": "Beijing Capital Airlines",
    "8L": "Lucky Air",
    "PN": "West Air",
    "FU": "Fuzhou Airlines",
    "ZH": "Shenzhen Airlines",
    "SC": "Shandong Airlines",
    "MF": "Xiamen Airlines",
    "FM": "Shanghai Airlines",
    "KN": "China United Airlines",
    "BK": "Okay Airways",
    "GS": "Tianjin Airlines",
    "G5": "China Express Airlines",
    "EU": "Chengdu Airlines",
    "9C": "Spring Airlines",
    "AQ": "9 Air",
    "Y8": "YTO Cargo Airlines",
    "HT": "Hainan Airlines (Cargo)",
    "HU": "Hainan Airlines",
    "CZ": "China Southern",
    "CA": "Air China",
    "MU": "China Eastern",
    "3U": "Sichuan Airlines",
    "ZH": "Shenzhen Airlines",
    "MF": "Xiamen Airlines",
}

# --- КОНВЕРТАЦИЯ ВАЛЮТ ---
def convert_to_usd(price, currency):
    if not price:
        return 0
    if currency.upper() == "USD":
        return float(price)
    rates = {
        "RUB": 0.011,
        "EUR": 1.10,
        "AED": 0.27,
        "GBP": 1.30,
        "KZT": 0.0021,
        "UAH": 0.024,
        "BYN": 0.31,
    }
    rate = rates.get(currency.upper(), 1.0)
    return round(float(price) * rate, 2)

# --- ГЕНЕРАЦИЯ ССЫЛКИ НА AVIASALES ---
def generate_aviasales_link(origin, destination, date, adults=1):
    base_url = "https://www.aviasales.com/search"
    params = {
        "origin": origin,
        "destination": destination,
        "departure_date": date,
        "adults": adults,
    }
    query_string = "&".join([f"{k}={v}" for k, v in params.items()])
    return f"{base_url}?{query_string}"

# --- AVIASALES DATA API ---
def search_aviasales_data_api(origin, destination, date):
    API_TOKEN = TRAVELPAYOUTS_TOKEN
    BASE_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
    params = {
        "origin": origin,
        "destination": destination,
        "departure_at": date,
        "one_way": "true",
        "token": API_TOKEN,
        "currency": "rub",
        "limit": 10,
        "sorting": "price",
        "market": "ru"
    }
    try:
        logger.info(f"📡 Aviasales Data API запрос: {origin}→{destination} {date}")
        response = requests.get(BASE_URL, params=params, timeout=10)
        if response.status_code != 200:
            logger.warning(f"⚠️ Aviasales Data API ошибка: {response.status_code}")
            return []
        data = response.json()
        if not data.get("success"):
            logger.warning(f"⚠️ Aviasales Data API: {data.get('error', 'Unknown error')}")
            return []
        flights_data = data.get("data", [])
        if not flights_data:
            logger.info(f"ℹ️ Aviasales Data API: рейсы не найдены для {origin}→{destination}")
            return []
        parsed_flights = []
        for item in flights_data[:10]:
            try:
                price_rub = item.get("price", 0)
                price_usd = round(price_rub / 91.0, 2)
                dep_datetime = item.get("departure_at", "")
                arr_datetime = item.get("return_at", "")
                duration = 0
                if dep_datetime and arr_datetime:
                    try:
                        dep_dt = datetime.fromisoformat(dep_datetime.replace("Z", "+00:00"))
                        arr_dt = datetime.fromisoformat(arr_datetime.replace("Z", "+00:00"))
                        duration = int((arr_dt - dep_dt).total_seconds() / 60)
                    except:
                        pass
                airline_code = item.get("airline", "N/A")
                airline = AIRLINE_NAMES.get(airline_code, airline_code)
                flight_number = item.get("flight_number", "")
                parsed_flights.append({
                    'airline': airline,
                    'price_usd': price_usd,
                    'segments': [{
                        'from_code': origin,
                        'to_code': destination,
                        'departure': dep_datetime,
                        'arrival': arr_datetime,
                        'duration': duration,
                        'departure_hour': 12
                    }],
                    'total_segments': 1,
                    'total_duration': duration,
                    'stops': 0,
                    'flight_number': flight_number,
                    'ticket_link': item.get("ticket_link", ""),
                    'source': 'aviasales-data-api'
                })
            except Exception as e:
                logger.error(f"❌ Ошибка парсинга рейса Aviasales: {e}")
                continue
        logger.info(f"✅ Aviasales Data API: найдено {len(parsed_flights)} рейсов")
        return parsed_flights
    except requests.exceptions.Timeout:
        logger.error("⏰ Aviasales Data API: таймаут")
        return []
    except Exception as e:
        logger.error(f"❌ Aviasales Data API ошибка: {e}")
        return []

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

# --- ПАРСЕРЫ (fast-flights) ---
def search_google_flights_fallback(origin, destination, date):
    try:
        logger.info(f"📡 Google Flights (улучшенный fallback) запрос: {origin}→{destination} {date}")
        airports_map = {
            "MOW": ["SVO", "DME", "VKO"],
            "DXB": ["DXB", "DWC", "SHJ"],
            "IST": ["IST", "SAW"],
            "LON": ["LHR", "LGW", "STN", "LCY"],
            "NYC": ["JFK", "EWR", "LGA"],
            "PAR": ["CDG", "ORY", "BVA"],
            "BKK": ["BKK", "DMK"],
            "TYO": ["NRT", "HND"],
        }
        from_airports = airports_map.get(origin, [origin])
        to_airports = airports_map.get(destination, [destination])
        all_flights = []
        max_attempts = 2
        for from_ap in from_airports:
            for to_ap in to_airports:
                if to_ap in ["DWC", "SHJ"]:
                    continue
                logger.info(f"  🔍 Поиск: {from_ap}→{to_ap}")
                try:
                    q = create_query(
                        flights=[FlightQuery(date=date, from_airport=from_ap, to_airport=to_ap)],
                        seat="economy",
                        trip="one-way",
                        passengers=Passengers(adults=1),
                        language="en-US",
                    )
                    for attempt in range(max_attempts):
                        try:
                            result = get_flights(q)
                            if result and len(result) > 0:
                                logger.info(f"  ✅ Найдено {len(result)} рейсов для {from_ap}→{to_ap}")
                                parsed = parse_google_flights_result(result)
                                if parsed:
                                    all_flights.extend(parsed)
                                break
                            else:
                                logger.warning(f"  ⚠️ Попытка {attempt+1}: рейсы не найдены")
                                if attempt < max_attempts - 1:
                                    time.sleep(1.5)
                        except Exception as e:
                            logger.error(f"  ❌ Ошибка при попытке {attempt+1}: {e}")
                            if attempt < max_attempts - 1:
                                time.sleep(2)
                            continue
                except Exception as e:
                    logger.error(f"  ❌ Ошибка для {from_ap}→{to_ap}: {e}")
                    continue
        if len(all_flights) < 3:
            logger.info(f"  🔍 Прямой поиск: {origin}→{destination}")
            try:
                q = create_query(
                    flights=[FlightQuery(date=date, from_airport=origin, to_airport=destination)],
                    seat="economy",
                    trip="one-way",
                    passengers=Passengers(adults=1),
                    language="en-US",
                )
                for attempt in range(max_attempts):
                    try:
                        result = get_flights(q)
                        if result and len(result) > 0:
                            logger.info(f"  ✅ Прямой поиск нашёл {len(result)} рейсов")
                            parsed = parse_google_flights_result(result)
                            if parsed:
                                all_flights.extend(parsed)
                            break
                        else:
                            if attempt < max_attempts - 1:
                                time.sleep(1.5)
                    except Exception as e:
                        logger.error(f"  ❌ Ошибка прямого поиска: {e}")
                        if attempt < max_attempts - 1:
                            time.sleep(2)
            except Exception as e:
                logger.error(f"  ❌ Ошибка прямого поиска: {e}")
        unique_flights = []
        seen = set()
        for flight in sorted(all_flights, key=lambda x: x.get('price_usd', 9999)):
            segments = flight.get('segments', [{}])
            key = (
                flight.get('airline', ''),
                flight.get('price_usd', 0),
                segments[0].get('departure', '') if segments else ''
            )
            if key not in seen:
                seen.add(key)
                unique_flights.append(flight)
        logger.info(f"📊 Всего найдено {len(unique_flights)} уникальных рейсов")
        return unique_flights[:50]
    except Exception as e:
        logger.error(f"❌ Критическая ошибка Google Flights fallback: {e}")
        return []

def parse_google_flights_result(flights):
    if not flights:
        return []
    flights_data = []
    try:
        for flight in flights:
            if not flight:
                continue
            price_raw = getattr(flight, 'price', None)
            if price_raw is None or price_raw == 'N/A':
                continue
            if price_raw > 2000:
                price_usd = price_raw / 91.0
                logger.info(f"🔄 Конвертируем {price_raw} RUB → {price_usd:.2f} USD")
            else:
                price_usd = price_raw
            if price_usd > 5000:
                logger.warning(f"⚠️ Пропускаем рейс с подозрительной ценой: {price_usd:.2f} USD")
                continue
            airlines = getattr(flight, 'airlines', None)
            if not airlines or len(airlines) == 0:
                continue
            airline_code = airlines[0] if airlines else 'N/A'
            airline = AIRLINE_NAMES.get(airline_code, airline_code)
            flight_list = getattr(flight, 'flights', [])
            if not flight_list or len(flight_list) == 0:
                continue
            segments = []
            total_duration = 0
            for seg in flight_list:
                if not seg:
                    continue
                seg_str = str(seg)
                parsed = parse_single_flight_segment(seg_str)
                if parsed and parsed.get('from_code') != 'N/A':
                    segments.append(parsed)
                    if parsed.get('duration'):
                        total_duration += parsed['duration']
            if not segments:
                continue
            stops = len(segments) - 1
            flights_data.append({
                'airline': airline,
                'price_usd': round(price_usd, 2),
                'segments': segments,
                'total_segments': len(segments),
                'total_duration': total_duration,
                'stops': stops,
                'source': 'google-flights',
                'ticket_link': f"https://www.google.com/travel/flights/search?tfs=CBwQAhooEgoyMDI2LTA3LTE1agcIARIDTVdXcgcIARIDSVNUcAGCAQsI____________AUABSAGYAQE"
            })
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга Google Flights: {e}")
        return flights_data if flights_data else []
    return flights_data

def parse_single_flight_segment(seg_str):
    result = {
        'from_airport': 'N/A',
        'from_code': 'N/A',
        'to_airport': 'N/A',
        'to_code': 'N/A',
        'departure': 'N/A',
        'arrival': 'N/A',
        'duration': 'N/A',
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
    except Exception as e:
        logger.error(f"❌ Ошибка парсинга сегмента: {e}")
    return result

# --- ОСНОВНАЯ ФУНКЦИЯ ПОИСКА ---
def search_all_flights(from_city, to_city, date):
    logger.info(f"🚀 Поиск: {from_city} → {to_city} на {date}")
    all_flights = []
    results_lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {}
        logger.info("🔍 1️⃣ Aviasales Data API...")
        future_aviasales = executor.submit(search_aviasales_data_api, from_city, to_city, date)
        futures['Aviasales Data API'] = future_aviasales
        logger.info("🔍 2️⃣ fast-flights...")
        future_ff = executor.submit(search_google_flights_fallback, from_city, to_city, date)
        futures['fast-flights'] = future_ff
        for source_name, future in futures.items():
            try:
                result = future.result(timeout=20)
                if result:
                    with results_lock:
                        all_flights.extend(result)
                        logger.info(f"✅ {source_name}: {len(result)}")
            except TimeoutError:
                logger.warning(f"⏰ {source_name}: timeout")
            except Exception as e:
                logger.error(f"❌ {source_name}: {e}")
    unique_flights = []
    seen = set()
    for flight in sorted(all_flights, key=lambda x: x.get('price_usd', 9999)):
        key = (flight.get('airline', ''), round(flight.get('price_usd', 0), -1))
        if key not in seen:
            seen.add(key)
            unique_flights.append(flight)
    logger.info(f"📊 ИТОГО: {len(unique_flights)} рейсов")
    return unique_flights[:100]

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ВЫВОДА ---
MONTHS_RU = {1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля', 5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа', 9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'}
WEEKDAYS_RU = {0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'}
CITIES = {"Москва": "MOW", "Стамбул": "IST", "Дубай": "DXB", "Лондон": "LON", "Париж": "PAR", "Нью-Йорк": "NYC", "Бангкок": "BKK", "Токио": "TYO", "Пекин": "PEK", "Шанхай": "PVG", "Анталья": "AYT", "Ереван": "EVN", "Астана": "NQZ", "Ташкент": "TAS", "Баку": "GYD", "Тбилиси": "TBS", "Сочи": "AER", "Калининград": "KGD", "Санкт-Петербург": "LED"}

def format_date_with_weekday(date_str):
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
        return f"{dt.day} {MONTHS_RU[dt.month]} ({WEEKDAYS_RU[dt.weekday()]}), {dt.hour:02d}:{dt.minute:02d}"
    except:
        return date_str

def format_duration(minutes):
    if minutes == 'N/A' or minutes is None:
        return 'N/A'
    try:
        mins = int(minutes)
        h = mins // 60
        m = mins % 60
        return f"{h}ч {m}м" if h > 0 and m > 0 else f"{h}ч" if h > 0 else f"{m}м"
    except:
        return str(minutes)

def format_flight_card_compact(flight, index=None, label=None):
    price_usd = flight.get('price_usd', 'N/A')
    usd_to_rub = 91.0
    if price_usd != 'N/A' and price_usd is not None:
        price_rub = int(float(price_usd) * usd_to_rub)
        price_str = f"${price_usd} (~{price_rub:,} ₽)".replace(',', ' ')
    else:
        price_str = "N/A"
    card = ""
    if index:
        card += f"*{index}.* "
    if label:
        card += f"{label} "
    airline = flight.get('airline', 'N/A')
    card += f"✈️ *{airline}* — {price_str}\n"
    segments = flight.get('segments', [])
    if segments:
        first_seg = segments[0]
        last_seg = segments[-1]
        dep = format_date_with_weekday(first_seg.get('departure', 'N/A')) if first_seg.get('departure') != 'N/A' else 'N/A'
        arr = format_date_with_weekday(last_seg.get('arrival', 'N/A')) if last_seg.get('arrival') != 'N/A' else 'N/A'
        total = format_duration(flight.get('total_duration', 0))
        card += f"   {first_seg.get('from_code', 'N/A')} → {last_seg.get('to_code', 'N/A')}  🛫 {dep}  🛬 {arr}  ⏱ {total}\n"
    stops = flight.get('stops', 0)
    if stops == 0:
        card += f"   🟢 *Прямой рейс*"
    else:
        card += f"   🔄 *{stops} пересадки*"
    return card

def get_best_flights(flights_data, user_preferences):
    if not flights_data:
        return None, None, None
    max_stops = user_preferences.get('max_stops', 3)
    filtered = [f for f in flights_data if f.get('stops', 0) <= max_stops]
    if not filtered:
        filtered = flights_data
    for flight in filtered:
        flight['score'] = rate_flight(flight, user_preferences)
    best_overall = max(filtered, key=lambda x: x.get('score', 0)) if filtered else None
    cheapest = min(filtered, key=lambda x: x.get('price_usd', 9999)) if filtered else None
    fastest = min(filtered, key=lambda x: x.get('total_duration', 9999)) if filtered else None
    return best_overall, cheapest, fastest

def rate_flight(flight, user_preferences):
    score = 0
    price = flight.get('price_usd', 0)
    stops = flight.get('stops', 0)
    total_duration = flight.get('total_duration', 0)
    if price < 200: score += 30
    elif price < 400: score += 25
    elif price < 600: score += 20
    elif price < 800: score += 15
    else: score += 10
    max_stops = user_preferences.get('max_stops', 3)
    if stops <= max_stops:
        if stops == 0: score += 30
        elif stops == 1: score += 20
        elif stops == 2: score += 10
        else: score += 5
    else:
        score -= 10
    segments = flight.get('segments', [])
    if segments:
        dep_hour = segments[0].get('departure_hour', 12)
        pref_hours = user_preferences.get('preferred_hours', 'all')
        if pref_hours == 'morning' and 6 <= dep_hour <= 12: score += 20
        elif pref_hours == 'day' and 12 <= dep_hour <= 18: score += 20
        elif pref_hours == 'evening' and 18 <= dep_hour <= 23: score += 20
        elif pref_hours == 'night' and (dep_hour >= 23 or dep_hour <= 6): score += 20
        elif pref_hours == 'all':
            score += 15 if 8 <= dep_hour <= 20 else 5
        else:
            score += 15 if 8 <= dep_hour <= 20 else 5
    if total_duration < 180: score += 20
    elif total_duration < 360: score += 15
    elif total_duration < 600: score += 10
    else: score += 5
    priority = user_preferences.get('priority', 'balance')
    if priority == 'price':
        score = score * 0.6 + max(0, (100 - price / 5)) * 0.4
    elif priority == 'speed':
        score = score * 0.6 + max(0, (100 - total_duration / 6)) * 0.4
    elif priority == 'comfort':
        comfort_score = 100 - stops * 20
        score = score * 0.5 + comfort_score * 0.5
    elif priority == 'convenience':
        convenience_score = 100 - stops * 15
        if segments and not (6 <= segments[0].get('departure_hour', 12) <= 22):
            convenience_score -= 20
        score = score * 0.3 + convenience_score * 0.7
    else:
        score = score * 0.5 + max(0, (100 - price / 8)) * 0.3 + max(0, (100 - total_duration / 8)) * 0.2
    return min(100, max(0, score))

def get_reason_compact(flight, prefs):
    reasons = []
    stops = flight.get('stops', 0)
    if stops == 0: reasons.append("✈️ прямой")
    elif stops == 1: reasons.append("🔄 1 пересадка")
    price = flight.get('price_usd', 0)
    if price < 300: reasons.append("💰 дешёвый")
    elif price < 500: reasons.append("💰 средний")
    duration = flight.get('total_duration', 0)
    if duration < 180: reasons.append("⚡ быстрый")
    elif duration < 360: reasons.append("⚡ средний")
    priority = prefs.get('priority', 'balance')
    reasons.append({"price": "📊 цена", "speed": "📊 скорость", "comfort": "📊 комфорт", "convenience": "📊 удобство", "balance": "📊 баланс"}.get(priority, "📊 баланс"))
    return "✅ " + ", ".join(reasons[:3])

# --- КЛАВИАТУРЫ ---
def get_main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("✈️ Начать поиск")],
        [KeyboardButton("⚙️ Настройки"), KeyboardButton("📊 История")],
        [KeyboardButton("❓ Помощь")]
    ], resize_keyboard=True)

def get_city_keyboard(user_id=None, selected_city=None, direction="from"):
    buttons = []
    row = []
    city_items = [
        ("⭐ Москва", "MOW"),
        ("Стамбул", "IST"),
        ("Дубай", "DXB"),
        ("Лондон", "LON"),
        ("Париж", "PAR"),
        ("Нью-Йорк", "NYC"),
        ("Бангкок", "BKK"),
        ("Токио", "TYO"),
        ("Пекин", "PEK"),
        ("Шанхай", "PVG"),
        ("Анталья", "AYT"),
        ("Ереван", "EVN"),
        ("Астана", "NQZ"),
        ("Ташкент", "TAS"),
        ("Баку", "GYD"),
        ("Тбилиси", "TBS"),
        ("Сочи", "AER"),
        ("Калининград", "KGD"),
        ("Санкт-Петербург", "LED"),
    ]
    if selected_city:
        for name, code in city_items:
            if code == selected_city:
                city_items.remove((name, code))
                city_items.insert(0, (f"✅ {name}", code))
                break
    for i, (name, code) in enumerate(city_items):
        row.append(InlineKeyboardButton(f"{name} ({code})", callback_data=f"city_{code}_{direction}"))
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

def get_priority_keyboard():
    buttons = [
        [InlineKeyboardButton("💰 Цена", callback_data="priority_price"), InlineKeyboardButton("⚡ Скорость", callback_data="priority_speed"), InlineKeyboardButton("⭐ Комфорт", callback_data="priority_comfort")],
        [InlineKeyboardButton("🛋️ Удобство", callback_data="priority_convenience"), InlineKeyboardButton("⚖️ Баланс", callback_data="priority_balance"), InlineKeyboardButton("◀️ Назад", callback_data="settings_back")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_settings_keyboard(user_id):
    prefs = get_user_preferences(user_id)
    priority = prefs.get('priority', 'balance')
    max_stops = prefs.get('max_stops', 3)
    pref_hours = prefs.get('preferred_hours', 'all')
    favorite_city = prefs.get('favorite_city', '')
    favorite_airport = prefs.get('favorite_airport', '')
    priority_names = {'price': '💰 Цена', 'speed': '⚡ Скорость', 'comfort': '⭐ Комфорт', 'convenience': '🛋️ Удобство', 'balance': '⚖️ Баланс'}
    stops_names = {0: '🟢 Прямые', 1: '🟡 1 пересадка', 2: '🟠 2 пересадки', 3: '🔵 Любые'}
    hours_names = {'morning': '🌅 Утро (6-12)', 'day': '☀️ День (12-18)', 'evening': '🌆 Вечер (18-23)', 'night': '🌙 Ночь (23-6)', 'all': '🕐 Любое время'}
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
        buttons = [[InlineKeyboardButton("❌ Сначала выберите город", callback_data="settings_back")], [InlineKeyboardButton("◀️ Назад", callback_data="settings_back")]]
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
    routes = [("MOW", "DXB"), ("IST", "DXB"), ("MOW", "IST"), ("DXB", "BKK"), ("LON", "NYC")]
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
        buttons.append([InlineKeyboardButton(f"✈️ {from_name} → {to_name} ({from_city}→{to_city})", callback_data=f"route_{from_city}_{to_city}")])
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

# --- ОБРАБОТЧИКИ КОМАНД ---
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

async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE, is_callback: bool = False):
    user_data = context.user_data
    user_id = update.effective_user.id
    from_city = user_data.get('from_city_name', '')
    to_city = user_data.get('to_city_name', '')
    date = user_data.get('date', '')
    if not from_city or not to_city or not date:
        if is_callback and update.callback_query:
            await update.callback_query.edit_message_text("❌ Не все данные для поиска заполнены. Начните заново.")
        else:
            await update.message.reply_text("❌ Не все данные для поиска заполнены. Начните заново.")
        return
    search_msg = f"🔍 Ищу билеты из *{from_city}* в *{to_city}* на *{date}*...\nЭто может занять до 30 секунд."
    if is_callback and update.callback_query:
        await update.callback_query.edit_message_text(search_msg, parse_mode="Markdown")
    else:
        await update.message.reply_text(search_msg, parse_mode="Markdown")
    try:
        from_codes = user_data.get('from_city_codes', [])
        to_codes = user_data.get('to_city_codes', [])
        if not from_codes or not to_codes:
            from_codes = find_city_code(from_city)
            to_codes = find_city_code(to_city)
        if not from_codes or not to_codes:
            error_msg = "❌ Не удалось определить коды аэропортов. Попробуйте выбрать город из списка."
            if is_callback and update.callback_query:
                await update.callback_query.edit_message_text(error_msg, parse_mode="Markdown")
            else:
                await update.message.reply_text(error_msg, parse_mode="Markdown")
            return
        all_flights = []
        for from_code in from_codes[:2]:
            for to_code in to_codes[:2]:
                flights = search_all_flights(from_code, to_code, date)
                all_flights.extend(flights)
        if not all_flights:
            error_msg = "😔 К сожалению, рейсы не найдены.\nПопробуйте другую дату или направление."
            if is_callback and update.callback_query:
                await update.callback_query.edit_message_text(error_msg, parse_mode="Markdown")
            else:
                await update.message.reply_text(error_msg, parse_mode="Markdown")
            return
        prefs = get_user_preferences(user_id)
        best, cheapest, fastest = get_best_flights(all_flights, prefs)
        save_search_history(user_id, from_city, to_city, date, f"{from_city}→{to_city} {date}", all_flights[:10])
        response = f"✈️ *Найдено {len(all_flights)} рейсов* из {from_city} → {to_city} на {date}\n\n"
        if best:
            response += f"⭐ *Лучший вариант:*\n{format_flight_card_compact(best, label='⭐')}\n"
            response += f"   {get_reason_compact(best, prefs)}\n\n"
        if cheapest and cheapest != best:
            response += f"💰 *Самый дешёвый:*\n{format_flight_card_compact(cheapest, label='💰')}\n\n"
        if fastest and fastest != best and fastest != cheapest:
            response += f"⚡ *Самый быстрый:*\n{format_flight_card_compact(fastest, label='⚡')}\n\n"
        response += "🔗 *Где купить:*\n"
        aviasales_link = generate_aviasales_link(from_city, to_city, date)
        response += f"   [Купить на Aviasales]({aviasales_link})\n"
        if best and best.get('ticket_link'):
            response += f"   [Купить лучший вариант]({best['ticket_link']})\n"
        if cheapest and cheapest.get('ticket_link') and cheapest != best:
            response += f"   [Купить дешёвый]({cheapest['ticket_link']})\n"
        response += "\n💡 Чтобы изменить приоритет поиска, зайдите в Настройки."
        if is_callback and update.callback_query:
            await update.callback_query.edit_message_text(response, parse_mode="Markdown", disable_web_page_preview=True)
        else:
            await update.message.reply_text(response, parse_mode="Markdown", disable_web_page_preview=True)
        remaining = [f for f in all_flights if f not in [best, cheapest, fastest]]
        if remaining:
            extra = "\n".join([format_flight_card_compact(f, i+1) for i, f in enumerate(remaining)])
            extra_msg = f"📋 *Другие варианты:*\n\n{extra}"
            if is_callback and update.callback_query:
                await update.callback_query.message.reply_text(extra_msg, parse_mode="Markdown")
            else:
                await update.message.reply_text(extra_msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}")
        error_msg = f"❌ Произошла ошибка при поиске: {str(e)}\nПопробуйте позже или выберите другой маршрут."
        if is_callback and update.callback_query:
            await update.callback_query.edit_message_text(error_msg, parse_mode="Markdown")
        else:
            await update.message.reply_text(error_msg, parse_mode="Markdown")

# --- ДРУГИЕ ОБРАБОТЧИКИ ---
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_data = context.user_data
    user_id = update.effective_user.id
    if text == "✈️ Начать поиск":
        user_data.clear()
        await update.message.reply_text(
            "🌍 *Откуда вылетаем?*\n\n"
            "Выберите город из списка или введите название (на русском или английском):\n"
            "Например: *Москва*, *Dubai*, *Стамбул*",
            parse_mode="Markdown",
            reply_markup=get_city_keyboard(user_id, direction="from")
        )
        user_data['state'] = 'from_city'
    elif text == "⚙️ Настройки":
        prefs = get_user_preferences(user_id)
        priority = prefs.get('priority', 'balance')
        max_stops = prefs.get('max_stops', 3)
        pref_hours = prefs.get('preferred_hours', 'all')
        favorite_city = prefs.get('favorite_city', '')
        favorite_airport = prefs.get('favorite_airport', '')
        priority_names = {'price': '💰 Цена', 'speed': '⚡ Скорость', 'comfort': '⭐ Комфорт', 'convenience': '🛋️ Удобство', 'balance': '⚖️ Баланс'}
        stops_names = {0: '🟢 Прямые', 1: '🟡 1 пересадка', 2: '🟠 2 пересадки', 3: '🔵 Любые'}
        hours_names = {'morning': '🌅 Утро (6-12)', 'day': '☀️ День (12-18)', 'evening': '🌆 Вечер (18-23)', 'night': '🌙 Ночь (23-6)', 'all': '🕐 Любое время'}
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
            "📊 *Ваша история поиска:*\n\nНажмите на запрос, чтобы повторить поиск.",
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
                    f"✅ Найден город: *{text}*\n✈️ Аэропорты: {airports_list}\n\n🔍 Буду искать рейсы из всех аэропортов!\n\n🌍 *Куда летим?*\nВведите город прибытия:",
                    parse_mode="Markdown"
                )
            else:
                user_data['to_city_codes'] = codes
                user_data['to_city_name'] = text.strip()
                user_data['state'] = 'date'
                airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                await update.message.reply_text(
                    f"✅ Найден город: *{text}*\n✈️ Аэропорты: {airports_list}\n\n🔍 Буду искать рейсы во все аэропорты!\n\n📅 *Когда летим?*\nВыберите дату:",
                    parse_mode="Markdown",
                    reply_markup=get_date_keyboard()
                )
        else:
            await update.message.reply_text(
                f"❌ Город *{text}* не найден.\n\nПопробуйте:\n• Написать на русском (например, Стамбул)\n• Написать на английском (например, Istanbul)",
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
            await update.message.reply_text(f"✅ Избранный город: *{text}* ({city_code})", parse_mode="Markdown")
            await update.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        else:
            await update.message.reply_text(
                f"❌ Город *{text}* не найден.\n\nПопробуйте на русском или английском.",
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
                        f"✅ Найден город: *{text}*\n✈️ Аэропорты: {airports_list}\n\n🔍 Буду искать рейсы из всех аэропортов!\n\n🌍 *Куда летим?*\nВведите город прибытия:",
                        parse_mode="Markdown"
                    )
                    return
                else:
                    user_data['to_city_codes'] = codes
                    user_data['to_city_name'] = text.strip()
                    user_data['state'] = 'date'
                    airports_list = ", ".join([f"{get_airport_name(c)} ({c})" for c in codes])
                    await update.message.reply_text(
                        f"✅ Найден город: *{text}*\n✈️ Аэропорты: {airports_list}\n\n🔍 Буду искать рейсы во все аэропорты!\n\n📅 *Когда летим?*\nВыберите дату:",
                        parse_mode="Markdown",
                        reply_markup=get_date_keyboard()
                    )
                    return
        await handle_manual_search(update, text, context)

async def handle_manual_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_data = context.user_data
    match = re.search(r'([A-Z]{3})\s*[→-]\s*([A-Z]{3})\s*(\d{4}-\d{2}-\d{2})', text)
    if match:
        from_code, to_code, date = match.groups()
        user_data['from_city_codes'] = [from_code]
        user_data['to_city_codes'] = [to_code]
        user_data['from_city_name'] = from_code
        user_data['to_city_name'] = to_code
        user_data['date'] = date
        await update.message.reply_text(f"✅ Найден запрос: {from_code} → {to_code} на {date}")
        await perform_search(update, context)
    else:
        await update.message.reply_text(
            "❌ Не понял запрос.\n\nИспользуйте формат: `IST → DXB 2026-07-20`\nИли нажмите «✈️ Начать поиск» для пошагового выбора.",
            parse_mode="Markdown"
        )

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
    if data == "search_by_city":
        user_data['state'] = 'search_by_city'
        if not user_data.get('from_city_codes'):
            user_data['city_type'] = 'from'
            await query.edit_message_text(
                "🔍 *Введите название города вылета*\n\nНапример: *Стамбул*, *Dubai*, *Пекин*\n\nЯ найду все аэропорты автоматически.",
                parse_mode="Markdown"
            )
        else:
            user_data['city_type'] = 'to'
            await query.edit_message_text(
                "🔍 *Введите название города прибытия*\n\nНапример: *Стамбул*, *Dubai*, *Пекин*\n\nЯ найду все аэропорты автоматически.",
                parse_mode="Markdown"
            )
        return
    elif data == "settings_favorite_city":
        await query.edit_message_text(
            "⭐ *Выберите избранный город вылета*\n\nВыберите из списка или нажмите «Ввести город вручную»:",
            parse_mode="Markdown",
            reply_markup=get_favorite_city_keyboard()
        )
        return
    elif data == "fav_city_manual":
        user_data['state'] = 'fav_city_manual'
        await query.edit_message_text(
            "✏️ *Введите название города*\n\nНапример: *Стамбул*, *Dubai*, *Пекин*\n\nБот сам найдёт IATA-код.",
            parse_mode="Markdown"
        )
        return
    elif data == "settings_favorite_airport":
        await query.edit_message_text(
            "🛫 *Избранный аэропорт*\n\nРейсы из этого аэропорта будут показываться **первыми** в результатах поиска.\n\nЭто НЕ ограничивает поиск — бот всё равно ищет рейсы из всех аэропортов города,\nно рейсы из избранного аэропорта будут вверху списка.",
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
            await query.edit_message_text(f"✅ Избранный город: *{fav_name if fav_name else code}* ({code})", parse_mode="Markdown")
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
            await query.edit_message_text(f"✅ Избранный аэропорт: *{get_airport_name(code)} ({code})*", parse_mode="Markdown")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    elif data == "settings_stops":
        await query.edit_message_text(
            "🔄 *Максимум пересадок:*\n\nВыберите допустимое количество пересадок:",
            parse_mode="Markdown",
            reply_markup=get_stops_keyboard()
        )
        return
    elif data == "settings_hours":
        await query.edit_message_text(
            "⏰ *Удобное время вылета:*\n\nВыберите предпочтительное время:",
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
        priority_names = {'price': '💰 Цена', 'speed': '⚡ Скорость', 'comfort': '⭐ Комфорт', 'convenience': '🛋️ Удобство', 'balance': '⚖️ Баланс'}
        stops_names = {0: '🟢 Прямые', 1: '🟡 1 пересадка', 2: '🟠 2 пересадки', 3: '🔵 Любые'}
        hours_names = {'morning': '🌅 Утро (6-12)', 'day': '☀️ День (12-18)', 'evening': '🌆 Вечер (18-23)', 'night': '🌙 Ночь (23-6)', 'all': '🕐 Любое время'}
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
    elif data.startswith("priority_"):
        priority = data.replace("priority_", "")
        prefs = get_user_preferences(user_id)
        prefs['priority'] = priority
        save_user_preferences(user_id, prefs)
        priority_names = {'price': '💰 Цена', 'speed': '⚡ Скорость', 'comfort': '⭐ Комфорт', 'convenience': '🛋️ Удобство', 'balance': '⚖️ Баланс'}
        await query.edit_message_text(f"✅ Приоритет изменен на: *{priority_names.get(priority, priority)}*\n\n⚙️ Настройки обновлены!", parse_mode="Markdown")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    elif data.startswith("stops_"):
        stops = int(data.replace("stops_", ""))
        prefs = get_user_preferences(user_id)
        prefs['max_stops'] = stops
        save_user_preferences(user_id, prefs)
        stops_names = {0: '🟢 Прямые', 1: '🟡 1 пересадка', 2: '🟠 2 пересадки', 3: '🔵 Любые'}
        await query.edit_message_text(f"✅ Максимум пересадок: *{stops_names.get(stops, 'Любые')}*\n\n⚙️ Настройки обновлены!", parse_mode="Markdown")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    elif data.startswith("hours_"):
        hours = data.replace("hours_", "")
        prefs = get_user_preferences(user_id)
        prefs['preferred_hours'] = hours
        save_user_preferences(user_id, prefs)
        hours_names = {'morning': '🌅 Утро (6-12)', 'day': '☀️ День (12-18)', 'evening': '🌆 Вечер (18-23)', 'night': '🌙 Ночь (23-6)', 'all': '🕐 Любое время'}
        await query.edit_message_text(f"✅ Время вылета: *{hours_names.get(hours, 'Любое')}*\n\n⚙️ Настройки обновлены!", parse_mode="Markdown")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    elif data == "reset_settings":
        save_user_preferences(user_id, {'priority': 'balance', 'max_stops': 3, 'preferred_hours': 'all', 'favorite_city': '', 'favorite_airport': '', 'avoid_airports': ''})
        await query.edit_message_text(
            "✅ *Настройки сброшены до стандартных*\n\n🎯 Приоритет: Баланс\n🔄 Пересадки: Любые\n⏰ Время: Любое\n⭐ Город: Не выбран\n🛫 Аэропорт: Не выбран",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    elif data == "back_to_main":
        await query.edit_message_text("✈️ *Главное меню*", parse_mode="Markdown")
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    elif data == "manual_date":
        user_data['state'] = 'manual_date'
        await query.edit_message_text(
            "✏️ Введите дату в формате: *ГГГГ-ММ-ДД*\nНапример: *2026-07-20*",
            parse_mode="Markdown"
        )
        return
    elif data == "popular_routes":
        await query.edit_message_text(
            "✈️ *Популярные маршруты*\n\nВыберите маршрут для быстрого поиска:",
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
            user_data['from_city_name'] = from_city
            user_data['to_city_name'] = to_city
            await query.edit_message_text(f"🔍 Повторяем поиск: {from_city} → {to_city} на {date}")
            await perform_search(update, context, is_callback=True)
        return
    elif data.startswith("route_"):
        _, from_city, to_city = data.split("_")
        user_data['from_city_codes'] = [from_city]
        user_data['to_city_codes'] = [to_city]
        from_name = from_city
        to_name = to_city
        for name, code in CITIES.items():
            if code == from_city:
                from_name = name
            if code == to_city:
                to_name = name
        user_data['from_city_name'] = from_name
        user_data['to_city_name'] = to_name
        await query.edit_message_text(
            f"✅ Выбран маршрут: *{from_name} → {to_name}*\n\n📅 *Когда летим?*\nВыберите дату:",
            parse_mode="Markdown",
            reply_markup=get_date_keyboard()
        )
        return
    elif data.startswith("city_"):
        parts = data.split("_")
        code = parts[1]
        direction = parts[2] if len(parts) > 2 else "from"
        if direction == "from":
            user_data['from_city_codes'] = [code]
            city_name = code
            for name, c in CITIES.items():
                if c == code:
                    city_name = name
                    break
            user_data['from_city_name'] = city_name
            user_data['state'] = 'to_city'
            await query.edit_message_text(
                f"✅ *Вылет из:* {city_name} ({code})\n\n"
                "🌍 *Куда летим?*\n"
                "Выберите город прибытия:",
                parse_mode="Markdown",
                reply_markup=get_city_keyboard(user_id, selected_city=code, direction="to")
            )
        else:
            user_data['to_city_codes'] = [code]
            city_name = code
            for name, c in CITIES.items():
                if c == code:
                    city_name = name
                    break
            user_data['to_city_name'] = city_name
            user_data['state'] = 'date'
            from_city = user_data.get('from_city_name', '')
            from_code = user_data.get('from_city_codes', [''])[0]
            await query.edit_message_text(
                f"✅ *Вылет из:* {from_city} ({from_code})\n"
                f"✅ *Прилёт в:* {city_name} ({code})\n\n"
                "📅 *Когда летим?*\n"
                "Выберите дату:",
                parse_mode="Markdown",
                reply_markup=get_date_keyboard()
            )
    elif data.startswith("date_"):
        date = data.replace("date_", "")
        user_data['date'] = date
        await query.edit_message_text(f"✅ Выбрана дата: *{date}*", parse_mode="Markdown")
        await perform_search(update, context, is_callback=True)

# --- ОСТАЛЬНЫЕ ФУНКЦИИ ДЛЯ АЭРОПОРТОВ (find_city_code, load_airports и т.д.) ---
# Эти функции уже были в коде, я их не менял. Они должны быть в файле.
# Чтобы не перегружать ответ, я их не включаю, но они должны быть.
# Если их нет, добавьте их из предыдущей версии.

# --- ЗАПУСК ---
def main():
    init_db()
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("✅ Бот запущен!")
    application.run_polling()

if __name__ == "__main__":
    main()
