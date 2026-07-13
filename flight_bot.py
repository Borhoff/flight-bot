import os
import logging
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from letsfg.local import search_local

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Токен бота (задайте позже на Render)
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "ВАШ_ТОКЕН_ОТ_BOTFATHER")

# Стартовая команда
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✈️ Привет! Я бот для поиска авиабилетов.\n\n"
        "Отправь мне запрос в формате:\n"
        "`Москва → Стамбул 2026-07-20`\n\n"
        "Или используй кнопки ниже для ввода.",
        parse_mode="Markdown"
    )

# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    await update.message.reply_text("🔍 Ищу билеты... Это может занять 20-40 секунд.")
    
    try:
        # Парсинг запроса: "Москва → Стамбул 2026-07-20"
        parts = text.split("→")
        if len(parts) != 2:
            await update.message.reply_text("❌ Неправильный формат. Используй: Город → Город Дата")
            return
            
        from_city = parts[0].strip()
        rest = parts[1].strip().split(" ")
        if len(rest) < 2:
            await update.message.reply_text("❌ Не указана дата. Используй: Город → Город ГГГГ-ММ-ДД")
            return
            
        to_city = rest[0].strip()
        date = rest[1].strip()
        
        # Поиск билетов через LetsFG
        offers = await search_local(from_city, to_city, date)
        
        if not offers:
            await update.message.reply_text("❌ Рейсы не найдены. Попробуйте другую дату или направление.")
            return
        
        # Формируем ответ
        response = f"✈️ Найдено {len(offers)} рейсов:\n\n"
        for i, offer in enumerate(offers[:5], 1):
            airlines = ", ".join(offer.airlines) if hasattr(offer, 'airlines') else "N/A"
            price = offer.price if hasattr(offer, 'price') else "N/A"
            currency = offer.currency if hasattr(offer, 'currency') else ""
            dep_time = offer.departure_time if hasattr(offer, 'departure_time') else "N/A"
            arr_time = offer.arrival_time if hasattr(offer, 'arrival_time') else "N/A"
            stops = offer.stops if hasattr(offer, 'stops') else "N/A"
            
            response += f"{i}. {airlines} - {price} {currency}\n"
            response += f"   Вылет: {dep_time} → Прилет: {arr_time}\n"
            response += f"   Пересадки: {stops}\n\n"
        
        response += "💡 Для покупки перейдите на сайт авиакомпании и найдите этот рейс."
        await update.message.reply_text(response)
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при поиске: {str(e)}. Попробуйте другой запрос.")

def main():
    # Создаем приложение
    app = Application.builder().token(TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запускаем бота
    print("✅ Бот запущен и готов к работе!")
    app.run_polling()

if __name__ == "__main__":
    main()