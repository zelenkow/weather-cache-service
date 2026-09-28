import sqlite3
import time

import httpx
import pytest

from solution import (
    fetch_with_retry,
    get_from_cache,
    get_weather,
    init_db,
    is_fresh,
    save_to_cache,
)


@pytest.fixture
def conn():
    """Изолированная БД в памяти для каждого теста."""
    with sqlite3.connect(":memory:", check_same_thread=False) as conn:
        init_db(conn)
        yield conn


def test_init_db_creates_table(conn):
    """Проверяет, что init_db создаёт таблицу weather."""
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row[0] for row in cursor.fetchall()}
    assert "weather" in tables


def test_get_from_cache_empty(conn):
    """Проверяет, что get_from_cache возвращает None, если города нет."""
    assert get_from_cache(conn, "Batumi") is None


def test_save_and_get_from_cache(conn):
    """Проверяет сохранение и чтение из кеша."""
    save_to_cache(conn, "Batumi", 41.64, 41.63, 24.3)
    cached = get_from_cache(conn, "Batumi")
    assert cached is not None
    temperature, updated_at = cached
    assert temperature == 24.3
    assert updated_at is not None


def test_is_fresh_true():
    """Проверяет, что свежие данные — True."""
    assert is_fresh(int(time.time())) is True


def test_is_fresh_false():
    """Проверяет, что старые данные — False."""
    old = int(time.time()) - 700
    assert is_fresh(old) is False


def test_fetch_with_retry_success(mocker):
    """Проверяет успешный запрос с первой попытки."""
    mock_response = mocker.Mock()
    mock_response.json.return_value = {"current_weather": {"temperature": 24.3}}
    mock_response.raise_for_status.return_value = None
    mocker.patch("httpx.get", return_value=mock_response)

    result = fetch_with_retry(41.64, 41.63)
    assert result == 24.3


def test_fetch_with_retry_fails_after_retries(mocker):
    """Проверяет, что после 3 ошибок бросается HTTPError."""
    mocker.patch("httpx.get", side_effect=httpx.HTTPError("fail"))
    mocker.patch("time.sleep")

    with pytest.raises(httpx.HTTPError):
        fetch_with_retry(41.64, 41.63)


def test_get_weather_cache_hit(conn, mocker):
    """Проверяет, что свежий кеш возвращается без запроса к API."""
    save_to_cache(conn, "Batumi", 41.64, 41.63, 24.3)
    mock_get = mocker.patch("httpx.get")

    temp, source = get_weather(conn, "Batumi", 41.64, 41.63)

    assert temp == 24.3
    assert source == "CACHE"
    mock_get.assert_not_called()


def test_get_weather_api_success(conn, mocker):
    """Проверяет, что при пустом кеше идём в API."""
    mock_response = mocker.Mock()
    mock_response.json.return_value = {"current_weather": {"temperature": 25.5}}
    mock_response.raise_for_status.return_value = None
    mocker.patch("httpx.get", return_value=mock_response)

    temp, source = get_weather(conn, "Batumi", 41.64, 41.63)

    assert temp == 25.5
    assert source == "API"

    cached = get_from_cache(conn, "Batumi")
    assert cached[0] == 25.5


def test_get_weather_stale_cache_on_api_error(conn, mocker):
    """Проверяет fallback на старый кеш при ошибке API."""
    old_time = int(time.time()) - 700
    with conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO weather (city, latitude, longitude, temperature, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("Batumi", 41.64, 41.63, 20.0, old_time),
        )

    mocker.patch("httpx.get", side_effect=httpx.HTTPError("fail"))
    mocker.patch("time.sleep")

    temp, source = get_weather(conn, "Batumi", 41.64, 41.63)

    assert temp == 20.0
    assert source == "STALE CACHE"


def test_get_weather_error_no_cache(conn, mocker):
    """Проверяет ERROR, если нет кеша и API недоступен."""
    mocker.patch("httpx.get", side_effect=httpx.HTTPError("fail"))
    mocker.patch("time.sleep")

    temp, source = get_weather(conn, "Batumi", 41.64, 41.63)

    assert temp is None
    assert source == "ERROR"
