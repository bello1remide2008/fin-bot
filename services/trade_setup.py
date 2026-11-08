
import math
import logging

import pandas as pd

from services.technical_analysis import (
    analyze_market,
    TechnicalAnalysisError
)


logger = logging.getLogger(__name__)


class TradeSetupError(Exception):
    pass


TIMEFRAME_SETTINGS = {
    "15m": {
        "milliseconds": 15 * 60 * 1000,
        "max_age_minutes": 45
    },
    "1h": {
        "milliseconds": 60 * 60 * 1000,
        "max_age_minutes": 150
    },
    "4h": {
        "milliseconds": 4 * 60 * 60 * 1000,
        "max_age_minutes": 600
    }
}


def _prepare_candles(candles, timeframe):
    if not isinstance(candles, list) or len(candles) < 200:
        raise TradeSetupError(
            f"{timeframe} requires at least 200 candles."
        )

    try:
        df = pd.DataFrame(candles)

        required = [
            "timestamp", "open", "high", "low", "close"
        ]

        if not all(column in df.columns for column in required):
            raise TradeSetupError(
                f"Invalid {timeframe} candle structure."
            )

        for column in required:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce"
            )

        df = df.dropna(subset=required)
        df = df.sort_values("timestamp")
        df = df.drop_duplicates(
            subset=["timestamp"],
            keep="last"
        ).reset_index(drop=True)

        if len(df) < 200:
            raise TradeSetupError(
                f"Insufficient valid {timeframe} candles."
            )

        prices = df[
            ["open", "high", "low", "close"]
        ].values

        if not all(
            math.isfinite(float(value))
            for row in prices
            for value in row
        ):
            raise TradeSetupError(
                f"Invalid price values in {timeframe} data."
            )

        if (
            (df["high"] < df["low"]).any()
            or (df["high"] < df["open"]).any()
            or (df["high"] < df["close"]).any()
            or (df["low"] > df["open"]).any()
            or (df["low"] > df["close"]).any()
        ):
            raise TradeSetupError(
                f"Inconsistent OHLC data in {timeframe} candles."
            )

        return df

    except TradeSetupError:
        raise

    except Exception as error:
        raise TradeSetupError(
            f"Could not process {timeframe} candles."
        ) from error


def _check_freshness(df, timeframe):
    now_ms = pd.Timestamp.now(tz="UTC").timestamp() * 1000

    latest_timestamp = float(
        df.iloc[-1]["timestamp"]
    )

    settings = TIMEFRAME_SETTINGS[timeframe]

    candle_close = (
        latest_timestamp + settings["milliseconds"]
    )

    age_minutes = (now_ms - candle_close) / 60000

    if age_minutes < 0:
        raise TradeSetupError(
            f"{timeframe} data contains a future candle."
        )

    if age_minutes > settings["max_age_minutes"]:
        raise TradeSetupError(
            f"{timeframe} market data is stale. "
            f"Last completed candle is approximately "
            f"{age_minutes:.0f} minutes old."
        )


def _get_analysis(df):
    records = df.to_dict("records")

    try:
        return analyze_market(records)

    except TechnicalAnalysisError as error:
        raise TradeSetupError(
            f"Technical analysis failed: {error}"
        ) from error


def _no_trade(
    reason,
    trend_4h=None,
    trend_1h=None,
    risk_percent=1.0,
    buy_score=0,
    sell_score=0,
    checks=None
):
    return {
        "status": "NO TRADE",
        "direction": None,
        "setup_type": None,
        "entry": None,
        "stop_loss": None,
        "take_profit_1": None,
        "take_profit_2": None,
        "take_profit_3": None,
        "risk_reward": ["1:1", "1:2", "1:3"],
        "risk_distance": None,
        "risk_percent": risk_percent,
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "checks": checks or {},
        "reason": reason
    }


def generate_trade_setup(
    candles_15m,
    candles_1h,
    candles_4h,
    pair="XAUUSD",
    risk_percent=1.0,
    require_fresh=True
):
    """
    Scored multi-timeframe strategy.

    4H: Higher-timeframe context (supportive, but sideways is not an
        automatic rejection).
    1H: Directional confirmation.
    15M: Momentum and entry trigger.

    A signal is returned only when its score and trigger requirements
    are met. This function calculates suggested levels; it never places
    or executes trades.
    """

    if not 0 < risk_percent <= 1:
        raise TradeSetupError(
            "Risk must be greater than 0 and no more than 1%."
        )

    # STEP 1: Validate candles.
    df_15m = _prepare_candles(candles_15m, "15m")
    df_1h = _prepare_candles(candles_1h, "1h")
    df_4h = _prepare_candles(candles_4h, "4h")

    if require_fresh:
        _check_freshness(df_15m, "15m")
        _check_freshness(df_1h, "1h")
        _check_freshness(df_4h, "4h")

    # STEP 2: Calculate indicators independently for each timeframe.
    analysis_15m = _get_analysis(df_15m)
    analysis_1h = _get_analysis(df_1h)
    analysis_4h = _get_analysis(df_4h)

    trend_4h = str(analysis_4h.get("trend", "Unknown"))
    trend_1h = str(analysis_1h.get("trend", "Unknown"))
    trend_15m = str(analysis_15m.get("trend", "Unknown"))

    # STEP 3: Read and validate 15-minute indicators.
    entry_candle = df_15m.iloc[-1]
    previous_candle = df_15m.iloc[-2]

    entry = float(entry_candle["close"])
    candle_open = float(entry_candle["open"])
    candle_high = float(entry_candle["high"])
    candle_low = float(entry_candle["low"])

    try:
        atr = float(analysis_15m["atr"])
        ema20 = float(analysis_15m["ema20"])
        rsi = float(analysis_15m["rsi"])
        macd = float(analysis_15m["macd"])
        macd_signal = float(analysis_15m["signal"])

        histogram_value = analysis_15m.get("histogram")
        if histogram_value is None:
            histogram_value = analysis_15m.get("macd_histogram")
        if histogram_value is None:
            raise KeyError("histogram")

        histogram = float(histogram_value)
    except (KeyError, TypeError, ValueError) as error:
        raise TradeSetupError(
            f"Missing or invalid 15-minute indicator: {error}"
        ) from error

    if not all(math.isfinite(value) for value in (
        entry, candle_open, candle_high, candle_low,
        atr, ema20, rsi, macd, macd_signal, histogram
    )):
        raise TradeSetupError("Invalid indicator values.")

    if atr <= 0 or entry <= 0:
        raise TradeSetupError("Invalid ATR or entry price.")

    # STEP 4: Detect price-action triggers.
    previous_window = df_15m.iloc[-21:-1]
    recent_window = df_15m.iloc[-11:-1]

    previous_resistance = float(previous_window["high"].max())
    previous_support = float(previous_window["low"].min())
    recent_low = float(recent_window["low"].min())
    recent_high = float(recent_window["high"].max())

    previous_close = float(previous_candle["close"])
    bullish_candle = entry > candle_open
    bearish_candle = entry < candle_open

    bullish_breakout = (
        entry > previous_resistance
        and previous_close <= previous_resistance
    )
    bearish_breakout = (
        entry < previous_support
        and previous_close >= previous_support
    )

    # A pullback may approach the EMA and turn back in the trend direction.
    bullish_pullback = (
        entry > ema20
        and candle_low <= ema20 + 0.20 * atr
        and entry - ema20 <= 0.80 * atr
        and bullish_candle
    )
    bearish_pullback = (
        entry < ema20
        and candle_high >= ema20 - 0.20 * atr
        and ema20 - entry <= 0.80 * atr
        and bearish_candle
    )

    bullish_macd = macd > macd_signal
    bearish_macd = macd < macd_signal
    bullish_histogram = histogram > 0
    bearish_histogram = histogram < 0
    bullish_rsi = 50 <= rsi <= 72
    bearish_rsi = 28 <= rsi <= 50
    price_above_ema = entry > ema20
    price_below_ema = entry < ema20

    # STEP 5: Score both directions. A sideways 4H trend gives no points,
    # but it no longer vetoes a valid lower-timeframe opportunity.
    buy_score = 0
    sell_score = 0
    buy_reasons = []
    sell_reasons = []

    if trend_4h == "Bullish":
        buy_score += 2
        buy_reasons.append("4H bullish context (+2)")
    elif trend_4h == "Bearish":
        sell_score += 2
        sell_reasons.append("4H bearish context (+2)")

    if trend_1h == "Bullish":
        buy_score += 2
        buy_reasons.append("1H bullish confirmation (+2)")
    elif trend_1h == "Bearish":
        sell_score += 2
        sell_reasons.append("1H bearish confirmation (+2)")

    if bullish_macd:
        buy_score += 1
        buy_reasons.append("15M MACD bullish (+1)")
    elif bearish_macd:
        sell_score += 1
        sell_reasons.append("15M MACD bearish (+1)")

    if bullish_histogram:
        buy_score += 1
        buy_reasons.append("15M MACD histogram positive (+1)")
    elif bearish_histogram:
        sell_score += 1
        sell_reasons.append("15M MACD histogram negative (+1)")

    if bullish_rsi:
        buy_score += 1
        buy_reasons.append("15M RSI supports bullish momentum (+1)")
    elif bearish_rsi:
        sell_score += 1
        sell_reasons.append("15M RSI supports bearish momentum (+1)")

    if price_above_ema:
        buy_score += 1
        buy_reasons.append("Price above EMA20 (+1)")
    elif price_below_ema:
        sell_score += 1
        sell_reasons.append("Price below EMA20 (+1)")

    if bullish_candle:
        buy_score += 1
        buy_reasons.append("15M bullish candle (+1)")
    elif bearish_candle:
        sell_score += 1
        sell_reasons.append("15M bearish candle (+1)")

    if bullish_breakout:
        buy_score += 2
        buy_reasons.append("15M resistance breakout (+2)")
    elif bullish_pullback:
        buy_score += 1
        buy_reasons.append("15M bullish EMA pullback (+1)")

    if bearish_breakout:
        sell_score += 2
        sell_reasons.append("15M support breakdown (+2)")
    elif bearish_pullback:
        sell_score += 1
        sell_reasons.append("15M bearish EMA pullback (+1)")

    # Require a meaningful 15M trigger. Momentum plus EMA position and
    # candle direction can qualify even when there is no formal breakout.
    bullish_momentum_trigger = (
        bullish_macd and bullish_histogram
        and price_above_ema and bullish_candle
    )
    bearish_momentum_trigger = (
        bearish_macd and bearish_histogram
        and price_below_ema and bearish_candle
    )

    buy_trigger = (
        bullish_breakout or bullish_pullback or bullish_momentum_trigger
    )
    sell_trigger = (
        bearish_breakout or bearish_pullback or bearish_momentum_trigger
    )

    minimum_score = 6
    checks = {
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "trend_15m": trend_15m,
        "buy_score": buy_score,
        "sell_score": sell_score,
        "minimum_score": minimum_score,
        "buy_trigger": buy_trigger,
        "sell_trigger": sell_trigger,
        "buy_reasons": buy_reasons,
        "sell_reasons": sell_reasons
    }

    # Do not manufacture a signal when both sides are equally plausible.
    if buy_score >= minimum_score and buy_trigger and buy_score >= sell_score + 2:
        direction = "BUY"
        setup_type = (
            "BREAKOUT" if bullish_breakout
            else "PULLBACK" if bullish_pullback
            else "MOMENTUM"
        )
        direction_score = buy_score
        direction_reasons = buy_reasons

    elif sell_score >= minimum_score and sell_trigger and sell_score >= buy_score + 2:
        direction = "SELL"
        setup_type = (
            "BREAKDOWN" if bearish_breakout
            else "PULLBACK" if bearish_pullback
            else "MOMENTUM"
        )
        direction_score = sell_score
        direction_reasons = sell_reasons

    else:
        if buy_score >= minimum_score and buy_trigger and sell_score >= buy_score - 1:
            reason = "Bullish and bearish evidence are too close; direction is unclear."
        elif sell_score >= minimum_score and sell_trigger and buy_score >= sell_score - 1:
            reason = "Bullish and bearish evidence are too close; direction is unclear."
        elif max(buy_score, sell_score) < minimum_score:
            reason = (
                f"Confirmation score is below {minimum_score}/11 "
                f"(BUY {buy_score}, SELL {sell_score})."
            )
        else:
            reason = "The score is sufficient, but the 15-minute entry trigger is missing."

        return _no_trade(
            reason,
            trend_4h=trend_4h,
            trend_1h=trend_1h,
            risk_percent=risk_percent,
            buy_score=buy_score,
            sell_score=sell_score,
            checks=checks
        )

    # STEP 6: Calculate structural stop loss and risk distance.
    if direction == "BUY":
        stop_loss = min(recent_low, entry - atr) - 0.2 * atr
        risk_distance = entry - stop_loss

        if risk_distance <= 0 or risk_distance > 3 * atr:
            return _no_trade(
                "BUY setup rejected: structural stop-loss distance exceeds 3 ATR.",
                trend_4h, trend_1h, risk_percent,
                buy_score, sell_score, checks
            )

        take_profit_1 = entry + risk_distance
        take_profit_2 = entry + 2 * risk_distance
        take_profit_3 = entry + 3 * risk_distance

    else:
        stop_loss = max(recent_high, entry + atr) + 0.2 * atr
        risk_distance = stop_loss - entry

        if risk_distance <= 0 or risk_distance > 3 * atr:
            return _no_trade(
                "SELL setup rejected: structural stop-loss distance exceeds 3 ATR.",
                trend_4h, trend_1h, risk_percent,
                buy_score, sell_score, checks
            )

        take_profit_1 = entry - risk_distance
        take_profit_2 = entry - 2 * risk_distance
        take_profit_3 = entry - 3 * risk_distance

    logger.info(
        "FINBOT SIGNAL | %s | %s | %s | Score: %s | Entry: %s",
        pair, direction, setup_type, direction_score, entry
    )

    return {
        "status": "SETUP FOUND",
        "pair": pair,
        "direction": direction,
        "setup_type": setup_type,
        "strategy": "SCORED AGGRESSIVE MULTI-TIMEFRAME",
        "timeframes": ["15m", "1h", "4h"],
        "trend_4h": trend_4h,
        "trend_1h": trend_1h,
        "trend_15m": trend_15m,
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit_1": take_profit_1,
        "take_profit_2": take_profit_2,
        "take_profit_3": take_profit_3,
        "risk_reward": ["1:1", "1:2", "1:3"],
        "risk_distance": risk_distance,
        "atr": atr,
        "rsi": rsi,
        "risk_percent": risk_percent,
        "confirmation_score": direction_score,
        "maximum_score": 11,
        "checks": checks,
        "reason": (
            f"{direction} selected with a confirmation score of "
            f"{direction_score}/11. " + "; ".join(direction_reasons)
        )
    }
