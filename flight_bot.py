import os
import logging
import re
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from fast_flights import FlightQuery, Passengers, create_query, get_flights

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен бота из переменной окружения
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# Стартовая команда
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

# Парсинг рейсов из ответа fast-flights
def parse_flight_data(result):
    """Извлекает информацию о рейсах из объекта Flights"""
    flights_data = []
    
    for flight in result:
        try:
            # Базовая информация
            price = getattr(flight, 'price', 'N/A')
            airlines = getattr(flight, 'airlines', [])
            airline = airlines[0] if airlines else 'N/A'
            
            # Получаем список перелетов
            flight_list = getattr(flight, 'flights', [])
            
            # Собираем информацию по каждому сегменту
            segments = []
            for seg in flight_list:
                # Парсим строковое представление SingleFlight
                seg_str = str(seg)
                
                # Извлекаем аэропорты
                from_match = re.search(r"from_airport=Airport\(name='([^']+)', code='([^']+)'\)", seg_str)
                to_match = re.search(r"to_airport=Airport\(name='([^']+)', code='([^']+)'\)", seg_str)
                dep_match = re.search(r"departure=SimpleDatetime\(date=\[(\d+), (\d+), (\d+)\], time=\[(\d+), (\d+)\]\)", seg_str)
                arr_match = re.search(r"arrival=SimpleDatetime\(date=\[(\d+), (\d+), (\d+)\], time=\[(\d+), (\d+)\]\)", seg_str)
                dur_match = re.search(r"duration=(\d+)", seg_str)
                
                from_airport = f"{from_match.group(1)} ({from_match.group(2)})" if from_match else "N/A"
                to_airport = f"{to_match.group(1)} ({to_match.group(2)})" if to_match else "N/A"
                
                if dep_match:
                    dep_time = f"{dep_match.group(4)}:{dep_match.group(5).zfill(2)}"
                    dep_date = f"{dep_match.group(1)}-{dep_match.group(2).zfill(2)}-{dep_match.group(3).zfill(2)}"
                    departure = f"{dep_date} {dep_time}"
                else:
                    departure = "N/A"
                    
                if arr_match:
                    arr_time = f"{arr_match.group(4)}:{arr_match.group(5).zfill(2)}"
                    arr_date = f"{arr_match.group(1)}-{arr_match.group(2).zfill(2)}-{arr_match.group(3).zfill(2)}"
                    arrival = f"{arr_date} {arr_time}"
                else:
                    arrival = "N/A"
                
                duration = dur_match.group(1) if dur_match else "N/A"
                
                segments.append({
                    'from': from_airport,
                    'to': to_airport,
                    'departure': departure,
                    'arrival': arrival,
                    'duration': duration,
                })
            
            flights_data.append({
                'airline': airline,
                'price': price,
                'segments': segments,
                'total_segments': len(segments),
            })
            
        except Exception as e:
            logging.error(f"Error parsing flight: {e}")
            continue
    
    return flights_data

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text("🔍 Ищу билеты... Это займет несколько секунд.")
    
    try:
        # Парсинг запроса
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
            await update.message.reply_text(
                "❌ Неправильный формат. Используй:\n"
                "`LHR → JFK 2026-07-20`"
            )
            return

        from_city, to_city, date = query_parts
        
        # Поиск билетов через fast-flights
        query = create_query(
            flights=[FlightQuery(date=date, from_airport=from_city, to_airport=to_city)],
            seat="economy",
            trip="one-way",
            passengers=Passengers(adults=1),
            language="en-US",
        )
        
        result = get_flights(query)
        
        if not result or len(result) == 0:
            await update.message.reply_text(
                f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}."
            )
            return
        
        # Парсим данные
        flights_data = parse_flight_data(result)
        
        if not flights_data:
            await update.message.reply_text("❌ Не удалось получить детали рейсов.")
            return
        
        # Формируем ответ
        response = f"✈️ Найдено {len(flights_data)} вариантов:\n\n"
        
        for i, flight in enumerate(flights_data[:5], 1):
            response += f"{i}. ✈️ {flight['airline']} - {flight['price']} USD\n"
            
            for j, seg in enumerate(flight['segments'], 1):
                response += f"   Сегмент {j}: {seg['from']} → {seg['to']}\n"
                response += f"      Вылет: {seg['departure']}\n"
                response += f"      Прилет: {seg['arrival']}\n"
                response += f"      В пути: {seg['duration']} мин\n"
            
            response += f"   Пересадок: {flight['total_segments'] - 1}\n\n"
        
        response += "💡 Для покупки перейдите на сайт авиакомпании."
        await update.message.reply_text(response)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

def main():
    app = Application.builder().token(TOKEN).connect_timeout(60).read_timeout(60).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
