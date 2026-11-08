
import logging
import math

import pandas as pd

from services.technical_analysis import (
    analyze_market,
    TechnicalAnalysisError
)


logger = logging.getLogger(__name__)


class BacktestError(Exception):
    pass


TIMEFRAME_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000
}


def _prepare_data(candles, timeframe):
    """Validate and prepare historical candles."""

    if not isinstance(candles, list):
        raise BacktestError(
            f"{timeframe} candles must be a list."
        )

    if len(candles) < 200:
        raise BacktestError(
            f"{timeframe} requires at least 200 candles."
        )

    df = pd.DataFrame(candles)

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close"
    ]

    if not all(column in df.columns for column in required):
        raise BacktestError(
            f"{timeframe} candles have missing columns."
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
    )

    df = df.reset_index(drop=True)

    if len(df) < 200:
        raise BacktestError(
            f"{timeframe} has fewer than 200 valid candles."
        )

    values = df[
        ["open", "high", "low", "close"]
    ].to_numpy()

    if not all(math.isfinite(float(v)) for v in values.flatten()):
        raise BacktestError(
            f"{timeframe} contains invalid prices."
        )

    if (values <= 0).any():
        raise BacktestError(
            f"{timeframe} contains non-positive prices."
        )

    invalid = (
        (df["high"] < df[["open", "close", "low"]].max(axis=1))
        |
        (df["low"] > df[["open", "close", "high"]].min(axis=1))
    )

    if invalid.any():
        raise BacktestError(
            f"{timeframe} contains inconsistent OHLC candles."
        )

    return df


def _closed_candles(df, cutoff_timestamp, timeframe):
    """
    Return only candles that had completely closed
    by the supplied timestamp.
    """

    interval = TIMEFRAME_MS[timeframe]

    return df[
        (df["timestamp"] + interval) <= cutoff_timestamp
    ]


def _analyse(df):
    """Run the existing technical-analysis engine."""

    if len(df) < 200:
        return None

    # Limit indicator input to 300 candles.
    # This retains enough history for EMA200.
    recent = df.tail(300)

    try:
        return analyze_market(
            recent.to_dict("records")
        )

    except TechnicalAnalysisError:
        return None

    except (ValueError, KeyError, TypeError):
        logger.exception("Technical analysis failed.")
        return None


def _generate_signal(
    df15,
    analysis15,
    analysis1h,
    analysis4h,
    pair
):
    """
    Generate a signal using:
    4H direction,
    1H confirmation,
    15M entry conditions.
    """

    if not all([
        analysis15,
        analysis1h,
        analysis4h
    ]):
        return None

    trend4h = analysis4h.get("trend")
    trend1h = analysis1h.get("trend")

    if trend4h not in ("Bullish", "Bearish"):
        return None

    if trend1h != trend4h:
        return None

    latest = df15.iloc[-1]
    previous = df15.iloc[-21:-1]

    if len(previous) < 20:
        return None

    entry_reference = float(latest["close"])

    atr = float(analysis15["atr"])
    ema20 = float(analysis15["ema20"])

    rsi = float(analysis15["rsi"])
    macd = float(analysis15["macd"])
    macd_signal = float(analysis15["signal"])
    histogram = float(analysis15["histogram"])

    if not all(math.isfinite(value) for value in [
        entry_reference,
        atr,
        ema20,
        rsi,
        macd,
        macd_signal,
        histogram
    ]):
        return None

    if entry_reference <= 0 or atr <= 0:
        return None

    previous_high = float(previous["high"].max())
    previous_low = float(previous["low"].min())

    candle_open = float(latest["open"])
    candle_high = float(latest["high"])
    candle_low = float(latest["low"])
    candle_close = float(latest["close"])

    if trend4h == "Bullish":

        momentum = (
            macd > macd_signal
            and histogram > 0
            and 50 < rsi < 72
        )

        breakout = candle_high > previous_high

        pullback = (
            abs(candle_close - ema20) <= 0.5 * atr
            and candle_close > candle_open
        )

        if not momentum or not (breakout or pullback):
            return None

        recent_low = float(
            df15.tail(10)["low"].min()
        )

        stop_loss = recent_low - (0.2 * atr)

        direction = "BUY"

    else:

        momentum = (
            macd < macd_signal
            and histogram < 0
            and 28 < rsi < 50
        )

        breakout = candle_low < previous_low

        pullback = (
            abs(candle_close - ema20) <= 0.5 * atr
            and candle_close < candle_open
        )

        if not momentum or not (breakout or pullback):
            return None

        recent_high = float(
            df15.tail(10)["high"].max()
        )

        stop_loss = recent_high + (0.2 * atr)

        direction = "SELL"

    # The signal is based on the candle that just closed.
    # Actual simulated entry is the next candle's open.
    risk_reference = abs(entry_reference - stop_loss)

    if risk_reference <= 0:
        return None

    if risk_reference > 3 * atr:
        return None

    return {
        "pair": pair,
        "direction": direction,
        "stop_loss": stop_loss,
        "atr": atr,
        "signal_timestamp": int(latest["timestamp"])
    }


def _simulate_trades(
    signals,
    df15,
    reward_ratio,
    starting_balance,
    risk_percent,
    spread,
    slippage,
    commission_per_trade
):
    """
    Simulate one independent strategy variant per
    reward-to-risk target.

    Only one trade can be open at a time.
    """

    balance = float(starting_balance)

    peak_balance = balance
    max_drawdown = 0.0

    trades = []
    equity_curve = [balance]

    last_exit_index = -1

    for signal in signals:

        entry_index = signal["entry_index"]

        if entry_index <= last_exit_index:
            continue

        if entry_index >= len(df15):
            continue

        entry_candle = df15.iloc[entry_index]

        direction = signal["direction"]

        raw_entry = float(entry_candle["open"])

        stop_loss = float(signal["stop_loss"])

        # Spread is the full quoted spread in price units.
        # Slippage is applied on each side.
        if direction == "BUY":
            entry = raw_entry + (spread / 2) + slippage
        else:
            entry = raw_entry - (spread / 2) - slippage

        if direction == "BUY":
            risk_distance = entry - stop_loss
            take_profit = entry + (risk_distance * reward_ratio)
        else:
            risk_distance = stop_loss - entry
            take_profit = entry - (risk_distance * reward_ratio)

        if risk_distance <= 0:
            continue

        # Maximum cash risk permitted for this trade.
        risk_budget = balance * (risk_percent / 100)

        # Adverse exit-side execution cost.
        exit_cost = (spread / 2) + slippage

        # Reserve commission before calculating position exposure.
        available_risk = risk_budget - commission_per_trade

        if available_risk <= 0:
            continue

        # Adjust exposure so a stop-loss, including modeled
        # exit costs and commission, does not exceed the risk budget.
        risk_amount = available_risk / (
                1 + (exit_cost / risk_distance)
        )

        if risk_amount <= 0:
            continue

        exit_price = None
        exit_reason = None
        exit_index = None

        # Begin checking from the entry candle.
        for j in range(entry_index, len(df15)):

            candle = df15.iloc[j]

            opening_price = float(candle["open"])
            high = float(candle["high"])
            low = float(candle["low"])

            if direction == "BUY":

                # A market opening below the stop produces
                # a worse fill than the requested stop.
                if opening_price <= stop_loss:
                    exit_price = opening_price
                    exit_reason = "STOP LOSS - GAP"
                    exit_index = j
                    break

                stop_hit = low <= stop_loss
                target_hit = high >= take_profit

            else:

                if opening_price >= stop_loss:
                    exit_price = opening_price
                    exit_reason = "STOP LOSS - GAP"
                    exit_index = j
                    break

                stop_hit = high >= stop_loss
                target_hit = low <= take_profit

            # Conservative assumption: stop is hit first
            # if both stop and target occur in one candle.
            if stop_hit:
                exit_price = stop_loss
                exit_reason = "STOP LOSS"
                exit_index = j
                break

            if target_hit:
                exit_price = take_profit
                exit_reason = "TAKE PROFIT"
                exit_index = j
                break

            candle = df15.iloc[j]

            high = float(candle["high"])
            low = float(candle["low"])

            if direction == "BUY":

                stop_hit = low <= stop_loss
                target_hit = high >= take_profit

            else:

                stop_hit = high >= stop_loss
                target_hit = low <= take_profit

            # Conservative assumption:
            # If both are hit within the same candle,
            # assume stop-loss was hit first.
            if stop_hit:
                exit_price = stop_loss
                exit_reason = "STOP LOSS"
                exit_index = j
                break

            if target_hit:
                exit_price = take_profit
                exit_reason = "TAKE PROFIT"
                exit_index = j
                break

        # Close any remaining open position at the
        # last available candle's closing price.
        if exit_price is None:

            exit_candle = df15.iloc[-1]

            exit_price = float(exit_candle["close"])

            exit_reason = "END OF DATA"

            exit_index = len(df15) - 1

        # Apply exit-side costs.
        if direction == "BUY":
            exit_fill = exit_price - (spread / 2) - slippage
            price_move = exit_fill - entry
        else:
            exit_fill = exit_price + (spread / 2) + slippage
            price_move = entry - exit_fill

        # Profit/loss after spread and slippage.
        price_pnl = risk_amount * (
                price_move / risk_distance
        )

        # Deduct round-trip commission.
        pnl = price_pnl - commission_per_trade

        net_r = pnl / risk_budget

        balance += pnl

        peak_balance = max(peak_balance, balance)

        if peak_balance > 0:
            drawdown = (
                (peak_balance - balance)
                / peak_balance
            ) * 100
        else:
            drawdown = 0

        max_drawdown = max(
            max_drawdown,
            drawdown
        )

        trades.append({
            "pair": signal["pair"],
            "direction": direction,
            "entry": round(entry, 5),
            "stop_loss": round(stop_loss, 5),
            "take_profit": round(take_profit, 5),
            "exit_price": round(exit_fill, 5),
            "exit_reason": exit_reason,
            "entry_timestamp": int(
                entry_candle["timestamp"]
            ),
            "exit_timestamp": int(
                df15.iloc[exit_index]["timestamp"]
            ),
            "reward_ratio": reward_ratio,
            "risk_amount": round(risk_amount, 2),
            "pnl": round(pnl, 2),
            "result_r": round(net_r, 3),
            "balance": round(balance, 2),
            "risk_budget": round(risk_budget, 2),
            "commission": round(commission_per_trade, 2),
            "gross_price_pnl": round(price_pnl, 2),
            "net_pnl": round(pnl, 2),
        })

        equity_curve.append(balance)

        last_exit_index = exit_index

    wins = [
        trade for trade in trades
        if trade["pnl"] > 0
    ]

    losses = [
        trade for trade in trades
        if trade["pnl"] < 0
    ]

    gross_profit = sum(
        trade["pnl"] for trade in wins
    )

    gross_loss = abs(sum(
        trade["pnl"] for trade in losses
    ))

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else None
    )

    total_trades = len(trades)

    win_rate = (
        len(wins) / total_trades * 100
        if total_trades else 0
    )

    average_r = (
        sum(t["result_r"] for t in trades) / total_trades
        if total_trades else 0
    )

    return {
        "reward_ratio": reward_ratio,
        "starting_balance": round(starting_balance, 2),
        "ending_balance": round(balance, 2),
        "net_profit": round(balance - starting_balance, 2),
        "total_trades": total_trades,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(win_rate, 2),
        "profit_factor": (
            round(profit_factor, 3)
            if profit_factor is not None
            else None
        ),
        "average_r": round(average_r, 3),
        "max_drawdown_percent": round(max_drawdown, 2),
        "trades": trades,
        "equity_curve": equity_curve
    }


def run_backtest(
    candles_15m,
    candles_1h,
    candles_4h,
    pair="XAUUSD",
    test_days=30,
    starting_balance=1000,
    risk_percent=1.0,
    spread=0.0,
    slippage=0.0,
    commission_per_trade=0.0
):

    """
    Run a historical backtest.

    spread and slippage are expressed in the instrument's
    price units. Zero means results are gross of execution
    costs and should not be treated as live performance.
    """
    if commission_per_trade < 0:
        raise BacktestError(
            "Commission cannot be negative."
        )
    if not 0 < risk_percent <= 1:
        raise BacktestError(
            "Risk must be greater than 0% and no more than 1%."
        )

    if starting_balance <= 0:
        raise BacktestError(
            "Starting balance must be greater than zero."
        )

    if test_days < 1:
        raise BacktestError(
            "test_days must be at least 1."
        )

    if spread < 0 or slippage < 0:
        raise BacktestError(
            "Spread and slippage cannot be negative."
        )

    df15 = _prepare_data(candles_15m, "15m")
    df1h = _prepare_data(candles_1h, "1h")
    df4h = _prepare_data(candles_4h, "4h")

    latest_close_time = (
        int(df15.iloc[-1]["timestamp"])
        + TIMEFRAME_MS["15m"]
    )

    test_start_time = (
        latest_close_time
        - (test_days * 24 * 60 * 60 * 1000)
    )

    signals = []

    # Each signal is generated after a completed 15m candle.
    for i in range(199, len(df15) - 1):

        current_candle = df15.iloc[i]

        signal_close_time = (
            int(current_candle["timestamp"])
            + TIMEFRAME_MS["15m"]
        )

        if signal_close_time < test_start_time:
            continue

        # Only higher-timeframe candles that have closed
        # before the signal are allowed.
        closed_1h = _closed_candles(
            df1h,
            signal_close_time,
            "1h"
        )

        closed_4h = _closed_candles(
            df4h,
            signal_close_time,
            "4h"
        )

        if len(closed_1h) < 200 or len(closed_4h) < 200:
            continue

        analysis15 = _analyse(
            df15.iloc[:i + 1]
        )

        analysis1h = _analyse(closed_1h)

        analysis4h = _analyse(closed_4h)

        if not all([
            analysis15,
            analysis1h,
            analysis4h
        ]):
            continue

        signal = _generate_signal(
            df15.iloc[:i + 1],
            analysis15,
            analysis1h,
            analysis4h,
            pair
        )

        if signal:

            signal["entry_index"] = i + 1

            signals.append(signal)

    results = {}

    for reward_ratio in (1, 2, 3):

        results[f"1:{reward_ratio}"] = _simulate_trades(
            signals=signals,
            df15=df15,
            reward_ratio=reward_ratio,
            starting_balance=starting_balance,
            risk_percent=risk_percent,
            spread=spread,
            slippage=slippage,
        commission_per_trade = commission_per_trade
        )

    return {
        "pair": pair,
        "strategy": "Aggressive Multi-Timeframe Strategy",
        "timeframes": ["4h", "1h", "15m"],
        "test_days": test_days,
        "signals_generated": len(signals),
        "risk_percent": risk_percent,
        "execution_costs_configured": (
            spread > 0 or slippage > 0
        ),
        "spread": spread,
        "slippage": slippage,
        "results": results,
        "commission_per_trade": commission_per_trade,
    }
