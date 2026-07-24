#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы библиотеки fli.
Ищет рейсы Москва (MOW) → Дубай (DXB) на 29 июля 2026 года.
"""

import logging
from datetime import datetime
from fli.models import (
    Airport,
    PassengerInfo,
    SeatType,
    MaxStops,
    SortBy,
    FlightSearchFilters,
    FlightSegment
)
from fli.search import SearchFlights

# Настройка логирования, чтобы видеть процесс
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_fli_search():
    """
    Тестовый поиск через fli
    """
    try:
        print("🛫 Начинаем тестовый поиск через fli...")
        print("-" * 50)
        
        # 1. Настраиваем параметры поиска
        filters = FlightSearchFilters(
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[
                FlightSegment(
                    # Используем IATA-коды через внутренние константы библиотеки
                    departure_airport=[[Airport.MOW, 0]],
                    arrival_airport=[[Airport.DXB, 0]],
                    travel_date="2026-07-29",
                )
            ],
            seat_type=SeatType.ECONOMY,
            stops=MaxStops.ANY,  # Ищем с любым количеством пересадок
            sort_by=SortBy.CHEAPEST,  # Сортируем по цене
        )
        
        # 2. Выполняем поиск
        print("🔍 Отправляем запрос к Google Flights...")
        search = SearchFlights()
        flights = search.search(filters)
        
        # 3. Проверяем результат
        if not flights:
            print("❌ Рейсы не найдены.")
            return
        
        print(f"✅ Найдено рейсов: {len(flights)}")
        print("-" * 50)
        
        # 4. Выводим первые 15 рейсов
        for i, flight in enumerate(flights[:15], 1):
            print(f"\n✈️ Рейс {i}:")
            print(f"  Авиакомпания: {flight.airline_name}")
            print(f"  Цена: ${flight.price:.2f}")
            print(f"  Пересадки: {flight.stops}")
            print(f"  Длительность: {flight.duration} мин.")
            
            # Информация о сегментах перелёта
            for j, leg in enumerate(flight.legs, 1):
                print(f"\n  Сегмент {j}:")
                print(f"    Рейс: {leg.airline.value}{leg.flight_number}")
                print(f"    Откуда: {leg.departure_airport.value} ({leg.departure_airport.airport_name})")
                print(f"    Вылет: {leg.departure_datetime.strftime('%Y-%m-%d %H:%M')}")
                print(f"    Куда: {leg.arrival_airport.value} ({leg.arrival_airport.airport_name})")
                print(f"    Прилёт: {leg.arrival_datetime.strftime('%Y-%m-%d %H:%M')}")
        
        print("\n" + "="*50)
        print("✅ Тест завершён успешно!")
        
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()

def test_multi_airport():
    """
    Дополнительный тест: поиск рейсов из нескольких аэропортов Москвы
    """
    try:
        print("\n" + "="*50)
        print("🛫 Дополнительный тест: поиск из нескольких аэропортов")
        print("-" * 50)
        
        # Ищем рейсы из всех аэропортов Москвы в Дубай
        filters = FlightSearchFilters(
            passenger_info=PassengerInfo(adults=1),
            flight_segments=[
                FlightSegment(
                    departure_airport=[
                        [Airport.SVO, 0],
                        [Airport.DME, 0],
                        [Airport.VKO, 0]
                    ],
                    arrival_airport=[[Airport.DXB, 0]],
                    travel_date="2026-07-29",
                )
            ],
            seat_type=SeatType.ECONOMY,
            stops=MaxStops.ANY,
            sort_by=SortBy.CHEAPEST,
        )
        
        search = SearchFlights()
        flights = search.search(filters)
        
        if not flights:
            print("❌ Рейсы не найдены для всех аэропортов.")
            return
        
        print(f"✅ Найдено рейсов из всех аэропортов Москвы: {len(flights)}")
        
        # Покажем первые 5 рейсов
        for i, flight in enumerate(flights[:5], 1):
            print(f"\n✈️ Рейс {i}: {flight.airline_name} — ${flight.price:.2f}")
            for leg in flight.legs:
                print(f"  {leg.departure_airport.value} → {leg.arrival_airport.value}")
        
    except Exception as e:
        print(f"❌ Ошибка в тесте multi-airport: {e}")

if __name__ == "__main__":
    # Запускаем основной тест
    test_fli_search()
    
    # Запускаем дополнительный тест с несколькими аэропортами
    test_multi_airport()
