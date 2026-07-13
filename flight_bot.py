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
        # Парсинг запроса: "SVO → PVG 2026-07-17"
        parts = text.split("→")
        if len(parts) != 2:
            await update.message.reply_text(
                "❌ Неправильный формат. Используй:\n"
                "`SVO → PVG 2026-07-17`"
            )
            return
            
        from_city = parts[0].strip().upper()
        rest = parts[1].strip().split(" ")
        if len(rest) < 2:
            await update.message.reply_text("❌ Не указана дата. Используй: SVO → PVG 2026-07-17")
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
        try:
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
            
            # Проверка результата
            if result is None:
                await update.message.reply_text(
                    "❌ API вернул None. Возможно, проблема с подключением.\n"
                    "Попробуйте позже или другой маршрут."
                )
                return
                
            if not hasattr(result, '__len__') or len(result) == 0:
                await update.message.reply_text(
                    f"❌ Рейсы не найдены для {from_city} → {to_city} на {date}.\n\n"
                    "Попробуйте:\n"
                    "• Другую дату (например, 2026-07-25)\n"
                    "• Другой маршрут (LHR → NYC)\n"
                    "• Убедитесь, что используете IATA-коды аэропортов: SVO, DME, PVG, IST"
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
            await update.message.reply_text(f"❌ Ошибка при поиске: {str(e)}")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при обработке запроса: {str(e)}")

def main():
    app = Application.builder().token(TOKEN).connect_timeout(60).read_timeout(60).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()
