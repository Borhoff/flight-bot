import os
import logging
import re
import json
from datetime import datetime
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from fast_flights import FlightQuery, Passengers, create_query, get_flights

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен бота из переменной окружения
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Курс USD к RUB
USD_TO_RUB = 95.0

# Месяцы на русском
MONTHS_RU = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
}

# Дни недели на русском (сокращенно)
WEEKDAYS_RU = {
    0: 'Пн', 1: 'Вт', 2: 'Ср', 3: 'Чт', 4: 'Пт', 5: 'Сб', 6: 'Вс'
}

def reset_webhook():
    """Сбрасывает вебхук при запуске бота"""
    try:
        bot = Bot(TOKEN)
        bot.delete_webhook()
        print("✅ Вебхук сброшен")
    except Exception as e:
        print(f"❌ Ошибка сброса вебхука: {e}")

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
        'from_airport': 'N/A',
        'from_code': 'N/A',
        'to_airport': 'N/A',
        'to_code': 'N/A',
        'departure': 'N/A',
        'arrival': 'N/A',
        'duration': 'N/A',
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
        
        arr_match = re.search(r"arrival=SimpleDatetime\(date=\[(\d+), (\d+), (\d+)\], time=\[(\d+), (\d+)\]\)", seg_str)
        if arr_match:
            year, month, day = arr_match.group(1), arr_match.group(2), arr_match.group(3)
            hour, minute = arr_match.group(4), arr_match.group(5)
            result['arrival'] = f"{year}-{month.zfill(2)}-{day.zfill(2)} {hour.zfill(2)}:{minute.zfill(2)}"
        
        dur_match = re.search(r"duration=(\d+)", seg_str)
        if dur_match:
            result['duration'] = dur_match.group(1)
    except Exception as e:
        logging.error(f"Error parsing segment: {e}")
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
            for seg in flight_list:
                seg_str = str(seg)
                parsed = parse_single_flight(seg_str)
                segments.append(parsed)
            flights_data.append({
                'airline': airline,
                'price_usd': price_usd,
                'segments': segments,
                'total_segments': len(segments),
            })
        except Exception as e:
            logging.error(f"Error parsing flight: {e}")
            continue
    return flights_data

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ Привет! Я бот для поиска авиабилетов.\n\n"
        "Отправь мне запрос в формате:\n"
        "`LHR → JFK 2026-07-20`\n\n"
        "Примеры:\n"
        "🇬🇧 LHR → 🇺🇸 JFK 2026-07-20\n"
        "🇫🇷 CDG → 🇦🇪 DXB 2026-07-20\n"
        "🇹🇷 IST → 🇬🇧 LHR 2026-07-20",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text("🔍 Ищу билеты... Это займет несколько секунд.")
    try:
        delimiters = ["→", "->", "-", "—", "–"]
        query_parts = None
        for delim in delimiters:
            if delim in text:
                parts = text.split(delim)
                if len(parts) >= 2:
                    from_part = parts[0].strip()
                    rest_part = " ".join(parts[1:]).strip()
                    rest_parts = rest_part.split(" ")
                    if len(rest_parts) >= 2:
                        to_city = rest_parts[0].strip().upper()
                        date = rest_parts[1].strip()
                        from_city = from_part.upper()
                        query_parts = (from_city, to_city, date)
                        break
        if not query_parts:
            match = re.search(r'([A-Z]{3})\s*[→\-–—>]\s*([A-Z]{3})\s+(\d{4}-\d{2}-\d{2})', text)
            if match:
                from_city = match.group(1).upper()
                to_city = match.group(2).upper()
                date = match.group(3)
                query_parts = (from_city, to_city, date)
        if not query_parts:
            await update.message.reply_text("❌ Неправильный формат. Используй: `LHR → JFK 2026-07-20`")
            return
        from_city, to_city, date = query_parts
        query = create_query(
            flights=[FlightQuery(date=date, from_airport=from_city, to_airport=to_city)],
            seat="economy",
            trip="one-way",
            passengers=Passengers(adults=1),
            language="en-US",
        )
        result = get_flights(query)
        if not result or len(result) == 0:
            await update.message.reply_text(f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}.")
            return
        flights_data = parse_flight_data(result)
        if not flights_data:
            await update.message.reply_text("❌ Не удалось получить детали рейсов.")
            return
        response = f"✈️ Найдено {len(flights_data)} вариантов:\n\n"
        for i, flight in enumerate(flights_data[:10], 1):
            price_usd = flight['price_usd']
            price_rub = int(price_usd * USD_TO_RUB) if price_usd != 'N/A' else 'N/A'
            response += f"{i}. ✈️ {flight['airline']} - {price_rub} ₽ ({price_usd} USD)\n"
            for j, seg in enumerate(flight['segments'], 1):
                dep = format_date_with_weekday(seg['departure']) if seg['departure'] != 'N/A' else 'N/A'
                arr = format_date_with_weekday(seg['arrival']) if seg['arrival'] != 'N/A' else 'N/A'
                dur = format_duration(seg['duration'])
                response += f"   {j}→ {seg['from_airport']} ({seg['from_code']}) → {seg['to_airport']} ({seg['to_code']})\n"
                response += f"      🛫 {dep}\n"
                response += f"      🛬 {arr}\n"
                response += f"      ⏱ {dur}\n"
            stops = flight['total_segments'] - 1
            if stops == 0:
                response += f"   🟢 Прямой рейс\n"
            else:
                response += f"   🔄 Пересадок: {stops}\n"
            response += "\n"
        response += "💡 Для покупки перейдите на сайт авиакомпании."
        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    reset_webhook()  # <--- Сброс вебхука при запуске
    app = Application.builder().token(TOKEN).connect_timeout(60).read_timeout(60).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
