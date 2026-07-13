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
        
        # === ВЫВОДИМ СЫРЫЕ ДАННЫЕ ДЛЯ ОТЛАДКИ ===
        # Показываем структуру первого рейса
        debug_info = "🔍 Сырые данные (для отладки):\n\n"
        if len(result) > 0:
            first_flight = result[0]
            debug_info += f"Тип объекта: {type(first_flight)}\n"
            debug_info += f"Все атрибуты: {dir(first_flight)}\n\n"
            
            # Пробуем получить данные как словарь
            try:
                if hasattr(first_flight, '__dict__'):
                    debug_info += f"__dict__: {json.dumps(first_flight.__dict__, default=str, indent=2)[:500]}\n"
            except:
                pass
            
            # Пробуем получить данные как строку
            debug_info += f"\nСтроковое представление: {str(first_flight)[:300]}"
        
        # Также выводим список рейсов с ценами
        response = f"✈️ Найдено {len(result)} рейсов:\n\n"
        for i, flight in enumerate(result[:10], 1):
            price = "N/A"
            currency = ""
            if hasattr(flight, 'price') and flight.price:
                price = flight.price
            if hasattr(flight, 'currency') and flight.currency:
                currency = flight.currency
            
            # Пробуем получить авиакомпанию
            airline = "N/A"
            if hasattr(flight, 'airline') and flight.airline:
                airline = flight.airline
            elif hasattr(flight, 'carrier') and flight.carrier:
                airline = flight.carrier
            
            response += f"{i}. {airline} - {price} {currency}\n"
        
        # Отправляем сначала отладочную информацию
        await update.message.reply_text(debug_info[:4000])
        
        # Затем список рейсов
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
