import os
import logging
import re
import json
import threading
import sqlite3
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from fast_flights import FlightQuery, Passengers, create_query, get_flights

# --- НАСТРОЙКА ---
logging.basicConfig(level=logging.INFO)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
USD_TO_RUB = 95.0

# --- FLASK (ДЛЯ RENDER) ---
app_web = Flask(__name__)

@app_web.route('/')
def index():
    return "✅ Бот работает!", 200

@app_web.route('/health')
def health():
    return "OK", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app_web.run(host="0.0.0.0", port=port, debug=False)

# --- БАЗА ДАННЫХ SQLITE ---
def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            priority TEXT DEFAULT 'balance',
            max_stops INTEGER DEFAULT 2,
            preferred_hours TEXT DEFAULT '6-23',
            avoid_airports TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица истории запросов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            from_city TEXT,
            to_city TEXT,
            date TEXT,
            result TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

def get_user_preferences(user_id):
    """Получает настройки пользователя"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT priority, max_stops, preferred_hours, avoid_airports FROM users WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'priority': row[0],
            'max_stops': row[1],
            'preferred_hours': row[2],
            'avoid_airports': row[3]
        }
    return {'priority': 'balance', 'max_stops': 2, 'preferred_hours': '6-23', 'avoid_airports': ''}

def save_user_preferences(user_id, preferences):
    """Сохраняет настройки пользователя"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, priority, max_stops, preferred_hours, avoid_airports)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, preferences.get('priority', 'balance'),
          preferences.get('max_stops', 2),
          preferences.get('preferred_hours', '6-23'),
          preferences.get('avoid_airports', '')))
    conn.commit()
    conn.close()

def save_search_history(user_id, from_city, to_city, date, result):
    """Сохраняет историю поиска"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO search_history (user_id, from_city, to_city, date, result)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, from_city, to_city, date, json.dumps(result)))
    conn.commit()
    conn.close()

def get_search_history(user_id, limit=10):
    """Получает историю поиска пользователя"""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT from_city, to_city, date, created_at FROM search_history
        WHERE user_id = ? ORDER BY created_at DESC LIMIT ?
    ''', (user_id, limit))
    rows = cursor.fetchall()
    conn.close()
    return rows

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
MONTHS_RU = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
}

WEEKDAYS_RU = {
    0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'
}

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
        'departure': 'N/A', 'arrival': 'N/A', 'duration': 'N/A'
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

# --- СИСТЕМА ОЦЕНКИ РЕЙСОВ ---
def rate_flight(flight, user_preferences):
    """
    Оценивает рейс по шкале 0-100
    Учитывает: цену, время, пересадки, комфорт
    """
    score = 0
    price = flight['price_usd']
    stops = flight['stops']
    total_duration = flight['total_duration']
    
    # Цена (чем дешевле, тем лучше)
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
    
    # Пересадки (чем меньше, тем лучше)
    if stops == 0:
        score += 30
    elif stops == 1:
        score += 20
    elif stops == 2:
        score += 10
    else:
        score += 5
    
    # Время вылета (удобное время)
    first_segment = flight['segments'][0]
    dep_hour = first_segment.get('departure_hour', 12)
    preferred_hours = user_preferences.get('preferred_hours', '6-23')
    try:
        pref_start, pref_end = map(int, preferred_hours.split('-'))
        if pref_start <= dep_hour <= pref_end:
            score += 20
        elif 6 <= dep_hour <= 22:
            score += 10
        else:
            score += 5
    except:
        if 8 <= dep_hour <= 20:
            score += 15
        else:
            score += 5
    
    # Время в пути
    if total_duration < 180:  # до 3 часов
        score += 20
    elif total_duration < 360:  # до 6 часов
        score += 15
    elif total_duration < 600:  # до 10 часов
        score += 10
    else:
        score += 5
    
    # Учет приоритета пользователя
    priority = user_preferences.get('priority', 'balance')
    if priority == 'price':
        # Цена важнее
        score = score * 0.6 + max(0, (100 - price / 5)) * 0.4
    elif priority == 'speed':
        # Скорость важнее
        score = score * 0.6 + max(0, (100 - total_duration / 6)) * 0.4
    elif priority == 'comfort':
        # Комфорт важнее (меньше пересадок и удобное время)
        comfort_score = 100 - stops * 20
        if 8 <= dep_hour <= 20:
            comfort_score += 10
        score = score * 0.5 + comfort_score * 0.5
    
    return min(100, max(0, score))

def format_flight_card(flight, index=None):
    """Форматирует одну карточку рейса"""
    price_usd = flight['price_usd']
    price_rub = int(price_usd * USD_TO_RUB) if price_usd != 'N/A' else 'N/A'
    
    card = ""
    if index:
        card += f"*{index}.* ✈️ {flight['airline']} - {price_rub} ₽ ({price_usd} USD)\n"
    else:
        card += f"✈️ {flight['airline']} - {price_rub} ₽ ({price_usd} USD)\n"
    
    for j, seg in enumerate(flight['segments'], 1):
        dep = format_date_with_weekday(seg['departure']) if seg['departure'] != 'N/A' else 'N/A'
        arr = format_date_with_weekday(seg['arrival']) if seg['arrival'] != 'N/A' else 'N/A'
        dur = format_duration(seg['duration'])
        card += f"   {j}→ {seg['from_airport']} ({seg['from_code']}) → {seg['to_airport']} ({seg['to_code']})\n"
        card += f"      🛫 {dep}\n"
        card += f"      🛬 {arr}\n"
        card += f"      ⏱ {dur}\n"
    
    stops = flight['stops']
    if stops == 0:
        card += f"   🟢 *Прямой рейс*"
    else:
        card += f"   🔄 *Пересадок: {stops}*"
    
    return card

def get_best_flights(flights_data, user_preferences):
    """
    Возвращает три варианта:
    1. Рекомендованный (по оценке)
    2. Самый дешевый
    3. Самый быстрый
    """
    if not flights_data:
        return None, None, None
    
    # Оцениваем каждый рейс
    for flight in flights_data:
        flight['score'] = rate_flight(flight, user_preferences)
    
    # Сортируем по разным критериям
    best_overall = max(flights_data, key=lambda x: x['score'])
    cheapest = min(flights_data, key=lambda x: x['price_usd'])
    fastest = min(flights_data, key=lambda x: x['total_duration'])
    
    return best_overall, cheapest, fastest

# --- КНОПКИ И ИНТЕРФЕЙС ---
def get_main_keyboard():
    """Главное меню с кнопками"""
    buttons = [
        [KeyboardButton("✈️ Начать поиск")],
        [KeyboardButton("⚙️ Настройки"), KeyboardButton("📊 История")],
        [KeyboardButton("❓ Помощь")]
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_city_keyboard():
    """Кнопки для выбора города"""
    buttons = []
    row = []
    for i, (name, code) in enumerate(CITIES.items()):
        row.append(InlineKeyboardButton(f"{name} ({code})", callback_data=f"city_{code}"))
        if (i + 1) % 2 == 0:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Кнопка "Ввести вручную"
    buttons.append([InlineKeyboardButton("✏️ Ввести IATA-код вручную", callback_data="manual_city")])
    return InlineKeyboardMarkup(buttons)

def get_date_keyboard():
    """Быстрый выбор даты"""
    today = datetime.now().date()
    buttons = [
        [InlineKeyboardButton(f"📅 Сегодня ({today.strftime('%d.%m')})", callback_data=f"date_{today.strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton(f"📅 Завтра ({(today + timedelta(days=1)).strftime('%d.%m')})", callback_data=f"date_{(today + timedelta(days=1)).strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton(f"📅 Через неделю ({(today + timedelta(days=7)).strftime('%d.%m')})", callback_data=f"date_{(today + timedelta(days=7)).strftime('%Y-%m-%d')}")],
        [InlineKeyboardButton("✏️ Ввести дату вручную", callback_data="manual_date")],
    ]
    return InlineKeyboardMarkup(buttons)

def get_settings_keyboard():
    """Кнопки настроек"""
    buttons = [
        [InlineKeyboardButton("💰 Приоритет: цена", callback_data="priority_price")],
        [InlineKeyboardButton("⚡ Приоритет: скорость", callback_data="priority_speed")],
        [InlineKeyboardButton("⭐ Приоритет: комфорт", callback_data="priority_comfort")],
        [InlineKeyboardButton("⚖️ Приоритет: баланс", callback_data="priority_balance")],
        [InlineKeyboardButton("🔄 Сбросить настройки", callback_data="reset_settings")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")]
    ]
    return InlineKeyboardMarkup(buttons)

def get_popular_routes():
    """Популярные маршруты для быстрого выбора"""
    routes = [
        ("LHR", "JFK"),
        ("CDG", "DXB"),
        ("IST", "LHR"),
        ("SIN", "SYD"),
        ("BKK", "ICN"),
        ("AMS", "FCO"),
    ]
    buttons = []
    for from_city, to_city in routes:
        buttons.append([InlineKeyboardButton(
            f"✈️ {from_city} → {to_city}",
            callback_data=f"route_{from_city}_{to_city}"
        )])
    buttons.append([InlineKeyboardButton("◀️ Назад", callback_data="back_to_main")])
    return InlineKeyboardMarkup(buttons)

# --- ОБРАБОТЧИКИ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    
    # Инициализация базы данных
    init_db()
    
    # Сохраняем пользователя
    user_id = update.effective_user.id
    get_user_preferences(user_id)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_data = context.user_data
    user_id = update.effective_user.id
    
    if text == "✈️ Начать поиск":
        await update.message.reply_text(
            "🌍 *Откуда вылетаем?*\n\n"
            "Выберите город из списка или введите IATA-код (3 буквы):\n"
            "Например: *LHR*, *JFK*, *CDG*",
            parse_mode="Markdown",
            reply_markup=get_city_keyboard()
        )
        user_data['state'] = 'from_city'
    
    elif text == "⚙️ Настройки":
        prefs = get_user_preferences(user_id)
        priority = prefs.get('priority', 'balance')
        priority_names = {
            'price': '💰 Цена',
            'speed': '⚡ Скорость',
            'comfort': '⭐ Комфорт',
            'balance': '⚖️ Баланс'
        }
        await update.message.reply_text(
            f"⚙️ *Ваши настройки:*\n\n"
            f"Приоритет: {priority_names.get(priority, 'Баланс')}\n"
            f"Максимум пересадок: {prefs.get('max_stops', 2)}\n"
            f"Удобное время: {prefs.get('preferred_hours', '6-23')}\n\n"
            "Выберите новый приоритет:",
            parse_mode="Markdown",
            reply_markup=get_settings_keyboard()
        )
    
    elif text == "📊 История":
        history = get_search_history(user_id)
        if not history:
            await update.message.reply_text("📊 У вас пока нет истории поиска.")
            return
        
        response = "📊 *Ваша история поиска:*\n\n"
        for from_city, to_city, date, created_at in history[:10]:
            created = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S.%f")
            response += f"✈️ {from_city} → {to_city} на {date}\n"
            response += f"   ⏱ {created.strftime('%d.%m.%Y %H:%M')}\n\n"
        
        await update.message.reply_text(response, parse_mode="Markdown")
    
    elif text == "❓ Помощь":
        help_text = (
            "✈️ *Как пользоваться ботом:*\n\n"
            "1️⃣ Нажмите *«Начать поиск»*\n"
            "2️⃣ Выберите город вылета\n"
            "3️⃣ Выберите город прибытия\n"
            "4️⃣ Выберите дату\n"
            "5️⃣ Получите 3 варианта:\n"
            "   ⭐ Лучший (рекомендованный)\n"
            "   💰 Самый дешевый\n"
            "   ⚡ Самый быстрый\n\n"
            "*Или отправьте запрос вручную:*\n"
            "`LHR → JFK 2026-07-20`"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")
    
    elif user_data.get('state') == 'manual_city':
        if len(text) == 3 and text.isupper():
            if user_data.get('city_type') == 'from':
                user_data['from_city'] = text
                user_data['state'] = 'to_city'
                await update.message.reply_text(
                    f"✅ Выбран вылет: *{text}*\n\n"
                    "🌍 *Куда летим?*\nВыберите город или введите IATA-код:",
                    parse_mode="Markdown",
                    reply_markup=get_city_keyboard()
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
    
    elif user_data.get('state') == 'manual_date':
        if re.match(r'\d{4}-\d{2}-\d{2}', text):
            user_data['date'] = text
            await update.message.reply_text(f"✅ Выбрана дата: *{text}*", parse_mode="Markdown")
            await perform_search(update, context)
        else:
            await update.message.reply_text("❌ Неправильный формат. Используй: ГГГГ-ММ-ДД (например, 2026-07-20)")
    
    else:
        # Пробуем распарсить ручной запрос
        await update.message.reply_text("🔍 Ищу билеты...")
        await handle_manual_search(update, text)

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_data = context.user_data
    user_id = update.effective_user.id
    
    if data == "back_to_main":
        await query.edit_message_text(
            "✈️ *Главное меню*\n\nВыберите действие:",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Нажмите кнопку:", reply_markup=get_main_keyboard())
        return
    
    elif data == "manual_city":
        user_data['state'] = 'manual_city'
        if user_data.get('state') == 'from_city' or not user_data.get('from_city'):
            user_data['city_type'] = 'from'
            await query.edit_message_text(
                "✏️ Введите IATA-код города вылета (3 буквы):\n"
                "Например: *LHR*, *JFK*, *CDG*",
                parse_mode="Markdown"
            )
        else:
            user_data['city_type'] = 'to'
            await query.edit_message_text(
                "✏️ Введите IATA-код города прибытия (3 буквы):\n"
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
    
    elif data.startswith("route_"):
        # Быстрый маршрут
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
        if user_data.get('state') == 'from_city' or not user_data.get('from_city'):
            user_data['from_city'] = code
            user_data['state'] = 'to_city'
            await query.edit_message_text(
                f"✅ Выбран вылет: *{code}*\n\n"
                "🌍 *Куда летим?*\nВыберите город:",
                parse_mode="Markdown",
                reply_markup=get_city_keyboard()
            )
        elif user_data.get('state') == 'to_city':
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
    
    elif data == "reset_settings":
        save_user_preferences(user_id, {'priority': 'balance', 'max_stops': 2, 'preferred_hours': '6-23', 'avoid_airports': ''})
        await query.edit_message_text(
            "✅ *Настройки сброшены до стандартных*\n\n"
            "Приоритет: Баланс\n"
            "Максимум пересадок: 2\n"
            "Удобное время: 6:00 - 23:00",
            parse_mode="Markdown"
        )
        await query.message.reply_text("👇 Выберите действие:", reply_markup=get_main_keyboard())
        return
    
    elif data == "popular_routes":
        await query.edit_message_text(
            "✈️ *Популярные маршруты*\n\n"
            "Выберите маршрут для быстрого поиска:",
            parse_mode="Markdown",
            reply_markup=get_popular_routes()
        )
        return

async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выполняет поиск и показывает 3 варианта"""
    user_data = context.user_data
    user_id = update.effective_user.id
    from_city = user_data.get('from_city')
    to_city = user_data.get('to_city')
    date = user_data.get('date')
    
    if not from_city or not to_city or not date:
        if hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text("❌ Не все данные введены. Начните заново.")
        else:
            await update.message.reply_text("❌ Не все данные введены. Нажмите «Начать поиск».")
        return
    
    try:
        if hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text("🔍 Ищу билеты... Это займет несколько секунд.")
            send_func = update.callback_query.edit_message_text
        else:
            send_func = update.message.reply_text
        
        # Поиск билетов
        q = create_query(
            flights=[FlightQuery(date=date, from_airport=from_city, to_airport=to_city)],
            seat="economy",
            trip="one-way",
            passengers=Passengers(adults=1),
            language="en-US",
        )
        result = get_flights(q)
        
        if not result or len(result) == 0:
            await send_func(f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}.")
            return
        
        # Парсим данные
        flights_data = parse_flight_data(result)
        if not flights_data:
            await send_func("❌ Не удалось получить детали рейсов.")
            return
        
        # Получаем предпочтения пользователя
        prefs = get_user_preferences(user_id)
        
        # Находим три варианта
        best_overall, cheapest, fastest = get_best_flights(flights_data, prefs)
        
        # Сохраняем в историю
        save_search_history(user_id, from_city, to_city, date, flights_data[:5])
        
        # Формируем ответ
        response = "✈️ *Результаты поиска:*\n\n"
        
        if best_overall:
            response += "⭐ *Лучший вариант (рекомендованный)*\n"
            response += format_flight_card(best_overall) + "\n"
            response += f"📌 *Почему:* {get_reason(best_overall, prefs)}\n\n"
        
        if cheapest:
            response += "💰 *Самый дешевый*\n"
            response += format_flight_card(cheapest) + "\n\n"
        
        if fastest:
            response += "⚡ *Самый быстрый*\n"
            response += format_flight_card(fastest) + "\n\n"
        
        response += "💡 Для покупки перейдите на сайт авиакомпании."
        
        await send_func(response, parse_mode="Markdown")
        
        # Сбрасываем состояние
        user_data.clear()
        await send_func("✈️ Поиск завершен! Нажмите *«Начать поиск»* для нового поиска.", parse_mode="Markdown", reply_markup=get_main_keyboard())
        
    except Exception as e:
        if hasattr(update, 'callback_query'):
            await update.callback_query.edit_message_text(f"❌ Ошибка: {str(e)}")
        else:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def get_reason(flight, prefs):
    """Объясняет, почему выбран этот рейс"""
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

async def handle_manual_search(update: Update, text):
    """Обработка ручного ввода"""
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
        await perform_search(update, context)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

# --- ГЛАВНАЯ ФУНКЦИЯ ---
def run_bot():
    reset_webhook()
    app = Application.builder().token(TOKEN).connect_timeout(60).read_timeout(60).build()
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_handler))
    
    print("✅ Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    # Инициализация базы данных
    init_db()
    
    # Запускаем веб-сервер для Render
    web_thread = threading.Thread(target=run_web_server)
    web_thread.daemon = True
    web_thread.start()
    run_bot()
