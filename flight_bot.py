import os
import logging
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
        "`SVO → PVG 2026-07-17`\n\n"
        "Используй IATA-коды аэропортов (3 буквы):\n"
        "SVO - Шереметьево, DME - Домодедово, VKO - Внуково\n"
        "PVG - Шанхай Пудун, IST - Стамбул, LHR - Лондон, NYC - Нью-Йорк",
        parse_mode="Markdown"
    )

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text("🔍 Ищу билеты... Это займет несколько секунд.")
    
    try:
        # Парсинг запроса
        parts = text.split("→")
        if len(parts) != 2:
            await update.message.reply_text("❌ Неправильный формат. Используй: SVO → PVG 2026-07-17")
            return
            
        from_city = parts[0].strip().upper()
        rest = parts[1].strip().split(" ")
        if len(rest) < 2:
            await update.message.reply_text("❌ Не указана дата.")
            return
            
        to_city = rest[0].strip().upper()
        date = rest[1].strip()
        
        # Проверка формата даты
        try:
            from datetime import datetime
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("❌ Неправильный формат даты. Используй ГГГГ-ММ-ДД")
            return
        
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
                f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}.\n"
                "Попробуйте другую дату или направление."
            )
            return
        
        # Формируем ответ с правильным парсингом
        response = f"✈️ Найдено {len(result)} рейсов:\n\n"
        
        for i, flight in enumerate(result[:5], 1):
            # Пробуем разные способы получить данные
            try:
                # Авиакомпания
                airline = "N/A"
                if hasattr(flight, 'airline') and flight.airline:
                    airline = flight.airline
                elif hasattr(flight, 'carrier') and flight.carrier:
                    airline = flight.carrier
                elif hasattr(flight, 'name') and flight.name:
                    airline = flight.name
                
                # Цена
                price = "N/A"
                currency = ""
                if hasattr(flight, 'price') and flight.price:
                    price = flight.price
                if hasattr(flight, 'currency') and flight.currency:
                    currency = flight.currency
                
                # Время вылета
                departure = "N/A"
                if hasattr(flight, 'departure') and flight.departure:
                    departure = flight.departure
                elif hasattr(flight, 'departure_time') and flight.departure_time:
                    departure = flight.departure_time
                elif hasattr(flight, 'outbound') and flight.outbound:
                    departure = flight.outbound
                
                # Время прилета
                arrival = "N/A"
                if hasattr(flight, 'arrival') and flight.arrival:
                    arrival = flight.arrival
                elif hasattr(flight, 'arrival_time') and flight.arrival_time:
                    arrival = flight.arrival_time
                elif hasattr(flight, 'inbound') and flight.inbound:
                    arrival = flight.inbound
                
                # Пересадки
                stops = "N/A"
                if hasattr(flight, 'stops') and flight.stops is not None:
                    stops = flight.stops
                elif hasattr(flight, 'stop_count') and flight.stop_count is not None:
                    stops = flight.stop_count
                
                response += f"{i}. {airline} - {price} {currency}\n"
                response += f"   Вылет: {departure} → Прилет: {arrival}\n"
                response += f"   Пересадки: {stops}\n\n"
                
            except Exception as e:
                logging.error(f"Error parsing flight {i}: {e}")
                response += f"{i}. Ошибка при парсинге рейса\n\n"
        
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
