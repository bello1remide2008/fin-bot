import math
import pandas as pd


class TechnicalAnalysisError(Exception):
    pass


def analyze_market(candles):
    """Analyze completed OHLC candles, oldest to newest.

    Trend is directional context, not a standalone entry signal.
    The caller is responsible for checking candle freshness.
    """
    if not isinstance(candles, list) or len(candles) < 200:
        raise TechnicalAnalysisError("At least 200 candles are required for comprehensive analysis.")

    required = ["timestamp", "open", "high", "low", "close"]
    df = pd.DataFrame(candles).copy()
    if not all(col in df.columns for col in required):
        raise TechnicalAnalysisError("Market data is missing required fields.")

    for column in required:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = (df.dropna(subset=required)
            .sort_values("timestamp")
            .drop_duplicates("timestamp", keep="last")
            .reset_index(drop=True))
    if len(df) < 200:
        raise TechnicalAnalysisError("Insufficient valid candles.")

    prices = df[["open", "high", "low", "close"]]
    if not all(math.isfinite(float(x)) for x in prices.to_numpy().flat):
        raise TechnicalAnalysisError("Market data contains invalid prices.")
    if (prices <= 0).any().any():
        raise TechnicalAnalysisError("Market data contains non-positive prices.")
    if ((df["high"] < df["low"]).any()
            or (df["high"] < df[["open", "close"]].max(axis=1)).any()
            or (df["low"] > df[["open", "close"]].min(axis=1)).any()):
        raise TechnicalAnalysisError("Market candles contain inconsistent prices.")

    close, high, low = df["close"], df["high"], df["low"]
    df["ema20"] = close.ewm(span=20, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False, min_periods=200).mean()

    # Wilder-smoothed RSI. Explicitly handle flat and one-sided markets.
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / 14, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / 14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50)
    df["rsi"] = rsi

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_histogram"] = df["macd"] - df["signal"]

    previous_close = close.shift(1)
    true_range = pd.concat([
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs()
    ], axis=1).max(axis=1)
    df["atr"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()

    latest = df.iloc[-1]
    price = float(latest["close"])
    ema20 = float(latest["ema20"])
    ema50 = float(latest["ema50"])
    ema200 = float(latest["ema200"])
    rsi_value = float(latest["rsi"])
    macd = float(latest["macd"])
    signal = float(latest["signal"])
    histogram = float(latest["macd_histogram"])
    atr = float(latest["atr"])
    values = [price, ema20, ema50, ema200, rsi_value, macd, signal, histogram, atr]
    if not all(math.isfinite(v) for v in values) or atr <= 0:
        raise TechnicalAnalysisError("Unable to calculate reliable indicators.")

    # ATR-scaled EMA separation avoids treating tiny EMA differences as
    # decisive, while allowing developing trends without perfect EMA stacking.
    ema_tolerance = 0.10 * atr
    ema20_slope = ema20 - float(df.iloc[-6]["ema20"])
    bullish_votes = {
        "price_above_ema20": price > ema20 + ema_tolerance,
        "ema20_above_ema50": ema20 > ema50 + ema_tolerance,
        "ema50_above_ema200": ema50 > ema200 + ema_tolerance,
        "ema20_rising": ema20_slope > ema_tolerance,
        "price_above_ema200": price > ema200 + ema_tolerance,
    }
    bearish_votes = {
        "price_below_ema20": price < ema20 - ema_tolerance,
        "ema20_below_ema50": ema20 < ema50 - ema_tolerance,
        "ema50_below_ema200": ema50 < ema200 - ema_tolerance,
        "ema20_falling": ema20_slope < -ema_tolerance,
        "price_below_ema200": price < ema200 - ema_tolerance,
    }
    bull_count = sum(bullish_votes.values())
    bear_count = sum(bearish_votes.values())

    # Require a short-term directional anchor and a meaningful vote margin.
    if bull_count >= 3 and bull_count >= bear_count + 2 and (
        bullish_votes["price_above_ema20"] or bullish_votes["ema20_rising"]
    ):
        trend = "Bullish"
        trend_strength = "Strong" if bull_count >= 4 and bear_count == 0 else "Developing"
    elif bear_count >= 3 and bear_count >= bull_count + 2 and (
        bearish_votes["price_below_ema20"] or bearish_votes["ema20_falling"]
    ):
        trend = "Bearish"
        trend_strength = "Strong" if bear_count >= 4 and bull_count == 0 else "Developing"
    else:
        trend = "Mixed / Sideways"
        trend_strength = "Unclear"

    if rsi_value >= 70:
        momentum = "Overbought RSI reading"
    elif rsi_value <= 30:
        momentum = "Oversold RSI reading"
    elif rsi_value > 55:
        momentum = "Positive momentum"
    elif rsi_value < 45:
        momentum = "Negative momentum"
    else:
        momentum = "Neutral momentum"

    recent = df.iloc[-51:-1]
    support = float(recent["low"].min())
    resistance = float(recent["high"].max())
    previous_window = df.iloc[-41:-21]
    current_window = df.iloc[-21:-1]
    higher_high = current_window["high"].max() > previous_window["high"].max()
    higher_low = current_window["low"].min() > previous_window["low"].min()
    lower_high = current_window["high"].max() < previous_window["high"].max()
    lower_low = current_window["low"].min() < previous_window["low"].min()
    if higher_high and higher_low:
        market_structure = "Rising price ranges"
    elif lower_high and lower_low:
        market_structure = "Falling price ranges"
    else:
        market_structure = "Mixed price structure"

    return {
        "price": price,
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "rsi": rsi_value,
        "macd": macd,
        "signal": signal,
        "macd_histogram": histogram,
        "histogram": histogram,
        "atr": atr,
        "trend": trend,
        "trend_strength": trend_strength,
        "trend_votes_bullish": bull_count,
        "trend_votes_bearish": bear_count,
        "momentum": momentum,
        "support": support,
        "resistance": resistance,
        "market_structure": market_structure,
        "timestamp": int(latest["timestamp"]),
    }
