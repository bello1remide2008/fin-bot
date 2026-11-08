import os
import re
import time
import asyncio
import logging
import math

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

API_KEY = os.getenv("MASSIVE_API_KEY")
BASE_URL = "https://api.massive.com"
MIN_CANDLES = 200

http_client = httpx.AsyncClient(
    timeout=httpx.Timeout(
        connect=10.0,
        read=25.0,
        write=10.0,
        pool=10.0
    ),
    limits=httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10
    )
)

market_cache = {}
historical_cache = {}
cache_locks = {}

CACHE_TTL = {
    "15m": 300,
    "1h": 600,
    "4h": 1800
}

# Massive Basic allows 5 API calls per minute.
# This lock keeps requests from this process below that limit.
massive_request_lock = asyncio.Lock()
last_massive_request_at = 0.0


class MarketDataError(Exception):
    pass


async def close_market_client():
    await http_client.aclose()


async def _historical_massive_request(url, params=None):
    """
    Make a rate-limited request to Massive.
    The lock also prevents simultaneous requests from this process.
    """
    global last_massive_request_at

    if not API_KEY:
        raise MarketDataError("MASSIVE_API_KEY is missing.")

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json"
    }

    async with massive_request_lock:
        elapsed = time.monotonic() - last_massive_request_at
        wait_time = max(0, 12.5 - elapsed)

        if wait_time:
            await asyncio.sleep(wait_time)

        try:
            response = await http_client.get(
                url,
                params=params,
                headers=headers,
                timeout=60
            )
            last_massive_request_at = time.monotonic()

            if response.status_code == 429:
                raise MarketDataError(
                    "Massive rate limit reached. Wait before trying again."
                )

            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as error:
            last_massive_request_at = time.monotonic()
            logger.exception("Massive historical HTTP error")
            raise MarketDataError(
                f"Massive returned HTTP {error.response.status_code}."
            ) from error

        except httpx.RequestError as error:
            last_massive_request_at = time.monotonic()
            logger.exception("Massive historical connection error")
            raise MarketDataError(
                "Could not connect to Massive. Check your internet connection."
            ) from error

        except ValueError as error:
            raise MarketDataError(
                "Massive returned an invalid JSON response."
            ) from error


async def get_forex_candles(
    pair="XAUUSD",
    timeframe="1h",
    limit=300
):
    if not API_KEY:
        raise MarketDataError(
            "MASSIVE_API_KEY is missing from .env"
        )

    pair = pair.upper().replace("/", "")

    if not re.fullmatch(r"[A-Z]{6}", pair):
        raise MarketDataError("Invalid market symbol.")

    # Backward-compatible timeframe aliases.
    timeframe = timeframe.lower().strip()

    aliases = {
        "minute": "15m",
        "hour": "1h"
    }

    timeframe = aliases.get(timeframe, timeframe)

    timeframe_config = {
        "15m": (15, "minute", 14, 15 * 60 * 1000),
        "1h": (1, "hour", 45, 60 * 60 * 1000),
        "4h": (4, "hour", 120, 4 * 60 * 60 * 1000)
    }

    if timeframe not in timeframe_config:
        raise MarketDataError(
            "Unsupported timeframe. Use 15m, 1h or 4h."
        )

    if not isinstance(limit, int) or limit < MIN_CANDLES:
        raise MarketDataError(
            f"At least {MIN_CANDLES} candles must be requested."
        )

    multiplier, timespan, lookback_days, interval_ms = (
        timeframe_config[timeframe]
    )

    ticker = f"C:{pair}"

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}"
        f"/range/{multiplier}/{timespan}"
        f"/{start:%Y-%m-%d}"
        f"/{end:%Y-%m-%d}"
    )

    params = {
        "adjusted": "true",
        "sort": "desc",
        "limit": limit
    }

    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }

    cache_key = (pair, timeframe, limit)

    lock = cache_locks.setdefault(
        cache_key,
        asyncio.Lock()
    )

    async with lock:
        cached = market_cache.get(cache_key)

        if cached:
            age = time.monotonic() - cached["stored_at"]

            if age < CACHE_TTL[timeframe]:
                return [
                    candle.copy()
                    for candle in cached["candles"]
                ]

        start_time = time.perf_counter()

        all_results = []
        next_url = url

        max_pages = 1
        pages_fetched = 0

        while next_url and pages_fetched < max_pages:
            try:
                response = await http_client.get(
                    next_url,
                    params=params if pages_fetched == 0 else None,
                    headers=headers
                )

                response.raise_for_status()
                data = response.json()

            except httpx.HTTPStatusError as error:
                status = error.response.status_code

                if status == 429:
                    retry_after = error.response.headers.get(
                        "Retry-After"
                    )

                    logger.warning(
                        "Massive rate limit reached. Retry-After: %s",
                        retry_after
                    )

                    raise MarketDataError(
                        "Massive API rate limit reached (HTTP 429). "
                        f"Retry-After: {retry_after or 'not provided'}. "
                        "Please wait before requesting market data again."
                    ) from error

                logger.error(
                    "Massive HTTP error: %s",
                    status
                )

                raise MarketDataError(
                    f"Massive returned HTTP {status}. "
                    "Check your subscription and API access."
                ) from error

            except httpx.RequestError as error:
                raise MarketDataError(
                    "Unable to connect to Massive."
                ) from error

            except ValueError as error:
                raise MarketDataError(
                    "Massive returned an invalid response."
                ) from error

            if data.get("status") == "ERROR":
                raise MarketDataError(
                    data.get(
                        "error",
                        "Market data request failed."
                    )
                )

            results = data.get("results") or []
            all_results.extend(results)

            pages_fetched += 1

            logger.info(
                "Massive %s %s | Page %s | Received %s candles",
                pair,
                timeframe,
                pages_fetched,
                len(results)
            )

            if len(all_results) >= limit:
                break

            next_url = data.get("next_url")

            if next_url:
                parsed = urlparse(next_url)

                if (
                    parsed.scheme != "https"
                    or parsed.hostname not in {
                        "api.massive.com",
                        "api.polygon.io"
                    }
                ):
                    raise MarketDataError(
                        "Unexpected pagination URL."
                    )

        if not all_results:
            raise MarketDataError(
                f"No historical candles returned for "
                f"{pair} ({timeframe})."
            )

        # Remove duplicate candles and validate required fields.
        unique = {}

        for candle in all_results:
            if not all(
                field in candle
                for field in ("t", "o", "h", "l", "c")
            ):
                continue

            try:
                timestamp = int(candle["t"])

                open_price = float(candle["o"])
                high_price = float(candle["h"])
                low_price = float(candle["l"])
                close_price = float(candle["c"])

                if not all(
                    math.isfinite(value)
                    for value in (
                        open_price,
                        high_price,
                        low_price,
                        close_price
                    )
                ):
                    continue

                if (
                    high_price < max(open_price, close_price, low_price)
                    or low_price > min(open_price, close_price, high_price)
                ):
                    continue

                unique[timestamp] = {
                    "timestamp": timestamp,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price
                }

            except (TypeError, ValueError, OverflowError):
                continue

        # Sort oldest to newest.
        candles = sorted(
            unique.values(),
            key=lambda item: item["timestamp"]
        )

        # Exclude candles that have not fully closed yet.
        now_ms = int(time.time() * 1000)

        candles = [
            candle
            for candle in candles
            if candle["timestamp"] + interval_ms <= now_ms
        ]

        # Keep the newest completed candles.
        candles = candles[-limit:]

        logger.info(
            "FINBOT %s %s | Completed candles: %s | "
            "Pages: %s | Time: %.2fs",
            pair,
            timeframe,
            len(candles),
            pages_fetched,
            time.perf_counter() - start_time
        )

        if len(candles) < MIN_CANDLES:
            raise MarketDataError(
                f"Insufficient completed historical data for {pair} "
                f"on {timeframe}.\n\n"
                f"Received: {len(candles)} candles\n"
                f"Required: {MIN_CANDLES}\n\n"
                "Check your Massive subscription, symbol coverage "
                "and historical data availability."
            )

        market_cache[cache_key] = {
            "stored_at": time.monotonic(),
            "candles": candles
        }

        return [
            candle.copy()
            for candle in candles
        ]


async def get_historical_forex_candles(
    pair="XAUUSD",
    timeframe="15m",
    start_date=None,
    end_date=None
):
    """
    Fetch historical forex candles for backtesting.

    Dates must use YYYY-MM-DD.
    Unlike get_forex_candles(), this function does not reject old candles
    using live-data freshness checks.
    """
    if not API_KEY:
        raise MarketDataError("MASSIVE_API_KEY is missing.")

    if not re.fullmatch(r"[A-Z]{6}", pair):
        raise MarketDataError("Invalid forex pair. Example: XAUUSD.")

    timeframe_settings = {
        "15m": (15, "minute"),
        "1h": (1, "hour"),
        "4h": (4, "hour")
    }

    if timeframe not in timeframe_settings:
        raise MarketDataError(
            "Unsupported timeframe. Use 15m, 1h, or 4h."
        )

    if not start_date or not end_date:
        raise MarketDataError(
            "Both start_date and end_date are required."
        )

    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError as error:
        raise MarketDataError(
            "Dates must use YYYY-MM-DD format."
        ) from error

    if start > end:
        raise MarketDataError(
            "start_date cannot be later than end_date."
        )

    if end > datetime.now(timezone.utc).date():
        raise MarketDataError(
            "Historical end_date cannot be in the future."
        )

    cache_key = (
        pair,
        timeframe,
        start_date,
        end_date
    )

    cached = historical_cache.get(cache_key)

    if cached:
        logger.info(
            "Using cached historical data: %s %s",
            pair,
            timeframe
        )
        return [dict(candle) for candle in cached]

    multiplier, timespan = timeframe_settings[timeframe]

    url = (
        f"{BASE_URL}/v2/aggs/ticker/C:{pair}/range/"
        f"{multiplier}/{timespan}/{start_date}/{end_date}"
    )

    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 50000
    }

    all_candles = []
    pages_fetched = 0
    max_pages = 20

    while url:
        if pages_fetched >= max_pages:
            raise MarketDataError(
                "Historical data requires too many pages. "
                "Reduce the requested date range."
            )

        data = await _historical_massive_request(
            url,
            params=params if pages_fetched == 0 else None
        )

        pages_fetched += 1

        if not isinstance(data, dict):
            raise MarketDataError(
                "Massive returned an unexpected response."
            )

        results = data.get("results", [])

        for candle in results:
            try:
                timestamp = int(candle["t"])

                open_price = float(candle["o"])
                high_price = float(candle["h"])
                low_price = float(candle["l"])
                close_price = float(candle["c"])

                values = [
                    open_price,
                    high_price,
                    low_price,
                    close_price
                ]

                if not all(math.isfinite(value) for value in values):
                    continue

                if min(values) <= 0:
                    continue

                if high_price < max(open_price, close_price, low_price):
                    continue

                if low_price > min(open_price, close_price, high_price):
                    continue

                all_candles.append({
                    "timestamp": timestamp,
                    "open": open_price,
                    "high": high_price,
                    "low": low_price,
                    "close": close_price
                })

            except (KeyError, TypeError, ValueError, OverflowError):
                logger.warning(
                    "Skipping an invalid historical candle."
                )
                continue

        next_url = data.get("next_url")

        if next_url:
            parsed = urlparse(next_url)

            if parsed.scheme != "https" or parsed.hostname != "api.massive.com":
                raise MarketDataError(
                    "Massive returned an unexpected pagination URL."
                )

            url = next_url
        else:
            url = None

    # Remove duplicate timestamps and sort oldest to newest.
    unique_candles = {}

    for candle in all_candles:
        unique_candles[candle["timestamp"]] = candle

    candles = sorted(
        unique_candles.values(),
        key=lambda item: item["timestamp"]
    )
    # Remove candles that have not fully closed.
    timeframe_ms = {
        "15m": 15 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000
    }

    now_ms = datetime.now(timezone.utc).timestamp() * 1000

    candles = [
        candle
        for candle in candles
        if candle["timestamp"] + timeframe_ms[timeframe] <= now_ms
    ]

    if len(candles) < MIN_CANDLES:
        raise MarketDataError(
            f"Only {len(candles)} historical candles were returned "
            f"for {pair} {timeframe}. At least {MIN_CANDLES} are required. "
            "Try an earlier start date."
        )

    historical_cache[cache_key] = [
        dict(candle) for candle in candles
    ]

    logger.info(
        "Fetched %s historical candles for %s %s (%s to %s)",
        len(candles),
        pair,
        timeframe,
        start_date,
        end_date
    )

    return [dict(candle) for candle in candles]