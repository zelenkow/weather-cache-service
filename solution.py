import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

db_lock = threading.Lock()

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"


CITIES = [
    ("Batumi", 41.64, 41.63),
    ("Tbilisi", 41.69, 44.80),
    ("Berlin", 52.52, 13.41),
    ("London", 51.50, -0.12),
]


WEATHER_TABLE = """
CREATE TABLE IF NOT EXISTS weather (
    city TEXT PRIMARY KEY,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    temperature REAL NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
"""


def get_weather(conn, city, lat, lon):
    cached = get_from_cache(conn, city)
    if cached and is_fresh(cached[1]):
        return cached[0], "CACHE"

    try:
        temp = fetch_with_retry(lat, lon)
        save_to_cache(conn, city, lat, lon, temp)
        return temp, "API"
    except httpx.HTTPError:
        if cached:
            return cached[0], "STALE CACHE"
        return None, "ERROR"


def init_db(conn):
    cursor = conn.cursor()
    cursor.executescript(WEATHER_TABLE)


def get_from_cache(conn, city):
    cursor = conn.cursor()
    cursor.execute("SELECT temperature, updated_at FROM weather WHERE city = ?", (city,))
    return cursor.fetchone()


def fetch_with_retry(lat, lon):
    delays = [1, 2, 4]
    for attempt in range(3):
        try:
            r = httpx.get(
                WEATHER_URL,
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current_weather": "true",
                },
                timeout=10,
            )
            r.raise_for_status()
            return r.json()["current_weather"]["temperature"]
        except httpx.HTTPError:
            if attempt < 2:
                time.sleep(delays[attempt])
    raise httpx.HTTPError("API недоступен после 3 попыток")


def save_to_cache(conn, city, lat, lon, temperature):
    with db_lock:
        with conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO weather (city, latitude, longitude, temperature, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (city, lat, lon, temperature, int(time.time())),
            )


def is_fresh(updated_at, ttl=600):
    return time.time() - updated_at < ttl


def main():
    with sqlite3.connect("weather.db", check_same_thread=False) as conn:
        init_db(conn)

        with ThreadPoolExecutor(max_workers=len(CITIES)) as executor:
            futures = []
            for city, lat, lon in CITIES:
                future = executor.submit(get_weather, conn, city, lat, lon)
                futures.append((city, future))

            for city, future in futures:
                temp, source = future.result()
                if temp is not None:
                    print(f"{city}: {temp}°C [{source}]")
                else:
                    print(f"{city}: нет данных [{source}]")


if __name__ == "__main__":
    main()
