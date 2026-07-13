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
        "`MOW → IST 2026-07-20`\n\n"
        "Используй IATA-коды городов (3 буквы):\n"
        "MOW - Москва, IST - Стамбул, LHR - Лондон, NYC - Нью-Йорк",
        parse_mode="Markdown"
    )

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text("🔍 Ищу билеты... Это займет несколько секунд.")
    
    try:
        # Парсинг запроса: "MOW → IST 2026-07-20"
        parts = text.split("→")
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ Неправильный формат. Используй:\n"
                "`MOW → IST 2026-07-20`"
            )
            return
            
        from_city = parts[0].strip().upper()
        rest = parts[1].strip().split(" ")
        if len(rest) < 2:
            await update.message.reply_text("❌ Не указана дата. Используй: MOW → IST 2026-07-20")
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
            flights=[
                FlightQuery(
                    date=date,
                    from_airport=from_city,
                    to_airport=to_city,
                ),
            ],
            seat="economy",
            trip="one-way",
            passengers=Passengers(adults=1),
            language="en-US",
        )
        
        result = get_flights(query)
        
        if not result:
            await update.message.reply_text(
                f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}.\n"
                "Попробуйте другую дату или направление."
            )
            return
        
        # Формируем ответ
        response = f"✈️ Найдено {len(result)} рейсов:\n\n"
        for i, flight in enumerate(result[:5], 1):
            airline = getattr(flight, 'airline', 'N/A')
            price = getattr(flight, 'price', 'N/A')
            currency = getattr(flight, 'currency', '')
            departure = getattr(flight, 'departure', 'N/A')
            arrival = getattr(flight, 'arrival', 'N/A')
            
            response += f"{i}. {airline} - {price} {currency}\n"
            response += f"   Вылет: {departure} → Прилет: {arrival}\n\n"
        
        response += "💡 Для покупки перейдите на сайт авиакомпании."
        await update.message.reply_text(response)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при поиске: {str(e)}. Попробуйте другой запрос.")

def main():
    app = Application.builder().token(TOKEN).connect_timeout(60).read_timeout(60).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
