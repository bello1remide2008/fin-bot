import math
import pandas as pd


class TechnicalAnalysisError(Exception):
    pass


def analyze_market(candles):

    if len(candles) < 200:
        raise TechnicalAnalysisError(
            "At least 200 candles are required "
            "for comprehensive analysis."
        )

    df = pd.DataFrame(candles).copy()

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close"
    ]

    if not all(col in df.columns for col in required):
        raise TechnicalAnalysisError(
            "Market data is missing required fields."
        )

    for column in ["open", "high", "low", "close"]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df["timestamp"] = pd.to_numeric(
        df["timestamp"],
        errors="coerce"
    )

    df = (
        df.dropna(subset=required)
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )

    if len(df) < 200:
        raise TechnicalAnalysisError(
            "Insufficient valid candles."
        )

    if not all(
        math.isfinite(value)
        for value in df[
            ["open", "high", "low", "close"]
        ].to_numpy().flatten()
    ):
        raise TechnicalAnalysisError(
            "Market data contains invalid prices."
        )

    if (
        (df["high"] < df["low"]).any()
        or (
            df["high"]
            < df[["open", "close"]].max(axis=1)
        ).any()
        or (
            df["low"]
            > df[["open", "close"]].min(axis=1)
        ).any()
    ):
        raise TechnicalAnalysisError(
            "Market candles contain inconsistent prices."
        )

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # =====================================
    # EXPONENTIAL MOVING AVERAGES
    # =====================================

    df["ema20"] = close.ewm(
        span=20,
        adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    df["ema200"] = close.ewm(
        span=200,
        adjust=False,
        min_periods=200
    ).mean()

    # =====================================
    # RSI (14)
    # =====================================

    delta = close.diff()

    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)

    avg_gain = gains.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        float("nan")
    )

    rsi = 100 - (100 / (1 + rs))

    rsi = rsi.mask(
        (avg_loss == 0) & (avg_gain > 0),
        100
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss > 0),
        0
    )

    rsi = rsi.mask(
        (avg_gain == 0) & (avg_loss == 0),
        50
    )

    df["rsi"] = rsi

    # =====================================
    # MACD
    # =====================================

    ema12 = close.ewm(
        span=12,
        adjust=False
    ).mean()

    ema26 = close.ewm(
        span=26,
        adjust=False
    ).mean()

    df["macd"] = ema12 - ema26

    df["signal"] = df["macd"].ewm(
        span=9,
        adjust=False
    ).mean()

    df["macd_histogram"] = (
        df["macd"] - df["signal"]
    )

    # =====================================
    # ATR (14)
    # =====================================

    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs()
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = true_range.ewm(
        alpha=1 / 14,
        adjust=False
    ).mean()

    # =====================================
    # LATEST DATA
    # =====================================

    latest = df.iloc[-1]

    price = float(latest["close"])

    ema20 = float(latest["ema20"])
    ema50 = float(latest["ema50"])
    ema200 = float(latest["ema200"])

    rsi_value = float(latest["rsi"])

    macd = float(latest["macd"])
    signal = float(latest["signal"])

    histogram = float(
        latest["macd_histogram"]
    )

    atr = float(latest["atr"])

    # =====================================
    # TREND DETECTION
    # =====================================

    if (
        price > ema20
        and ema20 > ema50
        and ema50 > ema200
    ):
        trend = "Bullish"

    elif (
        price < ema20
        and ema20 < ema50
        and ema50 < ema200
    ):
        trend = "Bearish"

    else:
        trend = "Mixed / Sideways"

    # =====================================
    # MOMENTUM
    # =====================================

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

    # =====================================
    # SUPPORT AND RESISTANCE
    # =====================================

    # Exclude the latest candle when
    # calculating previous reference levels.

    recent = df.iloc[-51:-1]

    support = float(
        recent["low"].min()
    )

    resistance = float(
        recent["high"].max()
    )

    # These are rolling price extremes,
    # not confirmed institutional levels.

    # =====================================
    # MARKET STRUCTURE
    # =====================================

    previous_window = df.iloc[-41:-21]
    current_window = df.iloc[-21:-1]

    higher_high = (
        current_window["high"].max()
        > previous_window["high"].max()
    )

    higher_low = (
        current_window["low"].min()
        > previous_window["low"].min()
    )

    lower_high = (
        current_window["high"].max()
        < previous_window["high"].max()
    )

    lower_low = (
        current_window["low"].min()
        < previous_window["low"].min()
    )

    if higher_high and higher_low:
        market_structure = "Rising price ranges"

    elif lower_high and lower_low:
        market_structure = "Falling price ranges"

    else:
        market_structure = "Mixed price structure"

    # =====================================
    # RETURN ANALYSIS
    # =====================================

    return {
        "price": price,

        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,

        "rsi": rsi_value,

        "macd": macd,
        "signal": signal,
        "macd_histogram": histogram,

        "atr": atr,

        "trend": trend,
        "momentum": momentum,

        "support": support,
        "resistance": resistance,

        "market_structure": market_structure,

        "timestamp": int(
            latest["timestamp"]
        )
    }