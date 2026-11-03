import os
import re
import time
import asyncio
import logging

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
cache_locks = {}

CACHE_TTL = {
    "minute": 30,
    "hour": 120,
    "day": 900
}


class MarketDataError(Exception):
    pass


async def close_market_client():
    await http_client.aclose()


async def get_forex_candles(
    pair="XAUUSD",
    timeframe="hour",
    limit=300
):

    if not API_KEY:
        raise MarketDataError(
            "MASSIVE_API_KEY is missing from .env"
        )

    pair = pair.upper().replace("/", "")

    if not re.fullmatch(r"[A-Z]{6}", pair):
        raise MarketDataError(
            "Invalid market symbol."
        )

    timeframe = timeframe.lower()

    timeframe_config = {
        "minute": (1, "minute", 14),
        "hour": (1, "hour", 90),
        "day": (1, "day", 730)
    }

    if timeframe not in timeframe_config:
        raise MarketDataError(
            "Unsupported timeframe."
        )

    if not isinstance(limit, int) or limit < MIN_CANDLES:
        raise MarketDataError(
            f"At least {MIN_CANDLES} candles must be requested."
        )

    multiplier, timespan, lookback_days = (
        timeframe_config[timeframe]
    )

    ticker = f"C:{pair}"

    end = datetime.now(timezone.utc)

    start = end - timedelta(
        days=lookback_days
    )

    url = (
        f"{BASE_URL}/v2/aggs/ticker/{ticker}"
        f"/range/{multiplier}/{timespan}"
        f"/{start:%Y-%m-%d}"
        f"/{end:%Y-%m-%d}"
    )

    params = {
        "adjusted": "true",
        "sort": "desc",
        "limit": 5000
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
            age = (
                time.monotonic()
                - cached["stored_at"]
            )

            if age < CACHE_TTL[timeframe]:
                return [
                    candle.copy()
                    for candle in cached["candles"]
                ]

        start_time = time.perf_counter()

        all_results = []

        next_url = url

        # Prevent unlimited API requests.
        max_pages = 5
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
                "Massive %s %s | Page %s | "
                "Received %s candles",
                pair,
                timeframe,
                pages_fetched,
                len(results)
            )

            # Enough records collected.
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

        # Remove duplicate candles.
        unique = {}

        for candle in all_results:

            if not all(
                field in candle
                for field in ("t", "o", "h", "l", "c")
            ):
                continue

            unique[candle["t"]] = {
                "timestamp": candle["t"],
                "open": candle["o"],
                "high": candle["h"],
                "low": candle["l"],
                "close": candle["c"]
            }

        candles = sorted(
            unique.values(),
            key=lambda item: item["timestamp"]
        )

        # Keep the newest candles.
        candles = candles[-limit:]

        logger.info(
            "FINBOT %s %s | Valid candles: %s | "
            "Pages: %s | Time: %.2fs",
            pair,
            timeframe,
            len(candles),
            pages_fetched,
            time.perf_counter() - start_time
        )

        if len(candles) < MIN_CANDLES:

            raise MarketDataError(
                f"Insufficient historical data for {pair} "
                f"on the {timeframe} timeframe.\n\n"
                f"Received: {len(candles)} candles\n"
                f"Required: {MIN_CANDLES}\n\n"
                "The data provider may have limited "
                "history or gaps for this symbol."
            )

        market_cache[cache_key] = {
            "stored_at": time.monotonic(),
            "candles": candles
        }

        return [
            candle.copy()
            for candle in candles
        ]