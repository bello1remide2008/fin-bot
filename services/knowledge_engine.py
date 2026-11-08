def generate_market_insight(analysis):

    insights = []
    suggestions = []

    trend = analysis["trend"]
    rsi = analysis["rsi"]
    macd = analysis["macd"]
    signal = analysis["signal"]

    # Trend analysis
    if trend == "Bullish":

        insights.append(
            "The market is showing bullish momentum."
        )

        suggestions.append(
            "Monitor nearby resistance levels "
            "and watch for trend confirmation."
        )

    elif trend == "Bearish":

        insights.append(
            "The market is showing bearish momentum."
        )

        suggestions.append(
            "Monitor nearby support levels "
            "and watch for possible stabilization."
        )

    else:

        insights.append(
            "The market currently shows mixed signals."
        )

        suggestions.append(
            "Consider waiting for clearer directional "
            "confirmation."
        )

    # RSI analysis
    if rsi >= 70:

        insights.append(
            "RSI is above 70, indicating strong "
            "upward momentum and potentially "
            "overbought conditions."
        )

        suggestions.append(
            "Watch for signs of weakening momentum."
        )

    elif rsi <= 30:

        insights.append(
            "RSI is below 30, indicating strong "
            "downward momentum and potentially "
            "oversold conditions."
        )

        suggestions.append(
            "Monitor for signs of stabilization."
        )

    # MACD analysis
    if macd > signal:

        insights.append(
            "MACD is above its signal line, "
            "suggesting positive momentum."
        )

    else:

        insights.append(
            "MACD is below its signal line, "
            "suggesting negative momentum."
        )

    return {
        "insights": insights,
        "suggestions": suggestions,
    }