import asyncio


from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from database.connection import (
    connect_database,
    close_database,
)
from database.connection import users_collection, transactions_collection
from services.trade_setup import (
    generate_trade_setup,
    TradeSetupError
)

from database.wallet_service import (
    register_user,
    get_wallets,
    record_transaction,
    get_transactions,
    get_summary,
)
from telegram.error import BadRequest

from dotenv import load_dotenv
from datetime import datetime, timezone, timedelta, date
from services.market_data import close_market_client
from decimal import Decimal, InvalidOperation
from services.llm_service import generate_ai_analysis, ask_finbot
from services.technical_analysis import analyze_market, TechnicalAnalysisError
from services.market_data import get_forex_candles,  get_historical_forex_candles,  MarketDataError
import time
import re
import os
import logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

# ============================================================
# 1. ENVIRONMENT VARIABLES
# ============================================================

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN is missing from .env")


# ============================================================
# 2. SETTINGS
# ============================================================

SUPPORTED_CURRENCIES = {
    "NGN": "₦",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
}

MAX_AMOUNT = Decimal("1000000000")


# ============================================================
# 5. MAIN MENU
# ============================================================

def main_menu():

    keyboard = [
        [
            InlineKeyboardButton(
                "💰 My Wallet",
                callback_data="wallet"
            ),
            InlineKeyboardButton(
                "📊 Market Trends",
                callback_data="markets"
            ),
        ],
        [
            InlineKeyboardButton(
                "📈 Analyze Currency",
                callback_data="analyze"
            ),
            InlineKeyboardButton(
                "📜 Transactions",
                callback_data="history"
            ),
        ],
        [
            InlineKeyboardButton(
                "📊 Financial Summary",
                callback_data="summary"
            ),
            InlineKeyboardButton(
                "🔔 Market Alerts",
                callback_data="alerts"
            ),
        ],
        [
            InlineKeyboardButton(
                "💎 Premium",
                callback_data="premium"
            ),
            InlineKeyboardButton(
                "❓ Help",
                callback_data="help"
            ),
        ],
    ]

    return InlineKeyboardMarkup(keyboard)


# ============================================================
# 6. BACK BUTTON
# ============================================================

def back_button():

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(

                    "⬅️ Back to Menu",
                    callback_data="menu"
                )
            ]
        ]
    )


# ============================================================
# 7. START COMMAND
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await register_user(update.effective_user)
    telegram_user = update.effective_user

    first_name = telegram_user.first_name or "there"

    message = (
        f"👋 Welcome to FinBot AI, {first_name}!\n\n"
        "💰 Your smart financial assistant.\n\n"
        "FinBot helps you:\n\n"
        "• Track income and expenses\n"
        "• Manage virtual currency wallets\n"
        "• Monitor financial markets\n"
        "• Analyze currency trends\n"
        "• Understand technical indicators\n"
        "• Receive market insights\n\n"
        "What would you like to do?"
    )

    await update.message.reply_text(
        message,
        reply_markup=main_menu()
    )


# ============================================================
# 8. MENU COMMAND
# ============================================================

async def menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 FINBOT AI\n\n"
        "Choose an option:",
        reply_markup=main_menu()
    )


# ============================================================
# 9. WALLET COMMAND
# ============================================================

async def wallet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    try:
        await register_user(update.effective_user)
        wallets = await get_wallets(user_id)

        message = (
            "📌 FinBot Personal Finance Tracker\n\n"
"Your balances are calculated from the income "
"and expenses you record here.\n\n"
"⚠️ These are tracking records only. "
"They are not real bank balances or withdrawable funds."
        )
        await update.message.reply_text(
            message,
            reply_markup=back_button()
        )

    except Exception as error:
        print(f"Wallet error: {error}")

        await update.message.reply_text(
            "❌ Unable to load your wallet right now. Please try again."
        )


# ============================================================
# 10. PARSE AMOUNT
# ============================================================

def parse_amount(value):

    try:

        amount = Decimal(value)

        if not amount.is_finite() or amount <= 0 or amount > MAX_AMOUNT:
            return None

        rounded = amount.quantize(Decimal("0.01"))
        if not amount.is_finite() or rounded != amount:
            return None
        return rounded

    except (InvalidOperation, ValueError):
        return None


# ============================================================
# 11. INCOME COMMAND
# ============================================================

async def income(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "💰 RECORD INCOME\n\n"
            "Usage:\n"
            "/income amount currency category\n\n"
            "Example:\n"
            "/income 50000 NGN salary\n\n"
            "Supported currencies:\n"
            "NGN, USD, EUR, GBP"
        )
        return

    amount = parse_amount(context.args[0])

    if amount is None:
        await update.message.reply_text(
            "❌ Please enter a valid positive amount."
        )
        return

    currency = "NGN"

    if len(context.args) >= 2:
        currency = context.args[1].upper()

    if currency not in SUPPORTED_CURRENCIES:
        await update.message.reply_text(
            "❌ Unsupported currency.\n\n"
            "Use NGN, USD, EUR or GBP."
        )
        return

    category = "Other"

    if len(context.args) >= 3:
        category = " ".join(context.args[2:]).title()

    try:
        await register_user(update.effective_user)

        result = await record_transaction(
            telegram_id=user_id,
            transaction_type="income",
            amount=amount,
            currency=currency,
            category=category
        )

        symbol = SUPPORTED_CURRENCIES[currency]

        await update.message.reply_text(
            "✅ INCOME RECORDED\n\n"
            f"Amount: {symbol}{result['amount']:,.2f}\n"
            f"Currency: {currency}\n"
            f"Category: {category}\n\n"
            "💰 New Balance:\n"
            f"{symbol}{result['balance']:,.2f}"
        )

    except ValueError as error:
        await update.message.reply_text(
            f"❌ {error}"
        )

    except Exception as error:
        print(f"Income error: {error}")

        await update.message.reply_text(
            "❌ Unable to record your income right now. Please try again."
        )


# ============================================================
# 12. EXPENSE COMMAND
# ============================================================

async def expense(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    if not context.args:
        await update.message.reply_text(
            "💸 RECORD EXPENSE\n\n"
            "Usage:\n"
            "/expense amount currency category\n\n"
            "Example:\n"
            "/expense 5000 NGN food"
        )
        return

    amount = parse_amount(context.args[0])

    if amount is None:
        await update.message.reply_text(
            "❌ Please enter a valid positive amount."
        )
        return

    currency = "NGN"

    if len(context.args) >= 2:
        currency = context.args[1].upper()

    if currency not in SUPPORTED_CURRENCIES:
        await update.message.reply_text(
            "❌ Unsupported currency.\n\n"
            "Use NGN, USD, EUR or GBP."
        )
        return

    category = "Other"

    if len(context.args) >= 3:
        category = " ".join(context.args[2:]).title()

    try:
        await register_user(update.effective_user)

        # Read the current balance from MongoDB.
        wallets = await get_wallets(user_id)
        balance = wallets[currency]

        if balance < amount:
            symbol = SUPPORTED_CURRENCIES[currency]

            await update.message.reply_text(
                "❌ INSUFFICIENT VIRTUAL BALANCE\n\n"
                f"Available: {symbol}{balance:,.2f}\n"
                f"Expense: {symbol}{amount:,.2f}"
            )
            return

        # MongoDB performs the actual balance deduction.
        result = await record_transaction(
            telegram_id=user_id,
            transaction_type="expense",
            amount=amount,
            currency=currency,
            category=category
        )

        symbol = SUPPORTED_CURRENCIES[currency]

        await update.message.reply_text(
            "✅ EXPENSE RECORDED\n\n"
            f"Amount: {symbol}{result['amount']:,.2f}\n"
            f"Category: {category}\n"
            f"Currency: {currency}\n\n"
            "💰 Remaining Balance:\n"
            f"{symbol}{result['balance']:,.2f}"
        )

    except ValueError as error:
        # Check the balance again in case another request changed it.
        try:
            wallets = await get_wallets(user_id)
            balance = wallets[currency]

            if balance < amount:
                symbol = SUPPORTED_CURRENCIES[currency]

                await update.message.reply_text(
                    "❌ INSUFFICIENT VIRTUAL BALANCE\n\n"
                    f"Available: {symbol}{balance:,.2f}\n"
                    f"Expense: {symbol}{amount:,.2f}"
                )
            else:
                await update.message.reply_text(f"❌ {error}")

        except Exception as inner_error:
            print(f"Expense validation error: {inner_error}")

            await update.message.reply_text(
                "❌ Unable to record your expense. Please try again."
            )

    except Exception as error:
        print(f"Expense error: {error}")

        await update.message.reply_text(
            "❌ Unable to record your expense right now. Please try again."
        )


# ============================================================
# FINBOT AI USER PROFILE
# ============================================================

async def profile(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    try:

        # Ensure user exists in MongoDB
        await register_user(update.effective_user)

        # Get user information
        user = await users_collection.find_one({
            "telegram_id": user_id
        })

        if not user:
            await update.message.reply_text(
                "❌ User profile not found."
            )
            return

        # Get wallet balances
        wallets = await get_wallets(user_id)

        # Count transactions
        total_transactions = await transactions_collection.count_documents({
            "telegram_id": user_id
        })

        # User details
        first_name = user.get("first_name") or "User"

        username = user.get("username")

        username_display = (
            f"@{username}"
            if username
            else "Not provided"
        )

        account_type = (
            "Premium"
            if user.get("is_premium", False)
            else "Free"
        )

        created_at = user.get("created_at")

        member_since = (
            created_at.strftime("%d %B %Y")
            if created_at
            else "Unknown"
        )

        # Build profile
        message = (
            "👤 FINBOT AI PROFILE\n\n"

            f"👋 Name: {first_name}\n"
            f"🔗 Username: {username_display}\n"
            f"🆔 Telegram ID: {user_id}\n\n"

            f"💎 Account Type: {account_type}\n"
            "🟢 Account Status: Active\n\n"

            "💰 WALLET INFORMATION\n"

            f"🇳🇬 NGN: ₦{wallets['NGN']:,.2f}\n"
            f"🇺🇸 USD: ${wallets['USD']:,.2f}\n"
            f"🇪🇺 EUR: €{wallets['EUR']:,.2f}\n"
            f"🇬🇧 GBP: £{wallets['GBP']:,.2f}\n\n"

            f"📊 Total Transactions: {total_transactions}\n\n"

            f"📅 Member Since: {member_since}\n\n"

            "🤖 Powered by FinBot AI"
        )

        await update.message.reply_text(
            message,
            reply_markup=back_button()
        )

    except Exception:

        logger.exception("Profile error")

        await update.message.reply_text(
            "❌ Unable to load your profile right now."
        )
# ============================================================
# 13. TRANSACTION HISTORY
# ============================================================

async def history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    try:
        await register_user(update.effective_user)

        transactions = await get_transactions(
            telegram_id=user_id,
            limit=10
        )

        if not transactions:
            await update.message.reply_text(
                "📜 TRANSACTION HISTORY\n\n"
                "You haven't recorded any transactions yet."
            )
            return

        message = "📜 RECENT TRANSACTIONS\n\n"

        for transaction in transactions:
            currency = transaction["currency"]
            symbol = SUPPORTED_CURRENCIES[currency]

            transaction_type = transaction["type"].lower()

            icon = (
                "🟢"
                if transaction_type == "income"
                else "🔴"
            )

            # MongoDB stores the amount in minor units.
            amount = Decimal(
                transaction["amount_minor"]
            ) / Decimal(100)

            date = transaction["created_at"].strftime(
                "%d %b %Y"
            )

            message += (
                f"{icon} {transaction_type.title()}\n"
                f"{symbol}{amount:,.2f}"
                f" • {transaction['category']}\n"
                f"{date}\n\n"
            )

        await update.message.reply_text(message)

    except Exception as error:
        print(f"History error: {error}")

        await update.message.reply_text(
            "❌ Unable to load your transaction history. Please try again."
        )


# ============================================================
# 14. FINANCIAL SUMMARY
# ============================================================

async def summary(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    try:
        await register_user(update.effective_user)

        # Retrieve saved transaction totals and wallet balances.
        totals = await get_summary(user_id)
        wallets = await get_wallets(user_id)

        message = "📊 FINANCIAL SUMMARY\n\n"

        has_activity = False

        for currency, symbol in SUPPORTED_CURRENCIES.items():
            income_value = totals[currency]["income"]
            expense_value = totals[currency]["expense"]

            if income_value == 0 and expense_value == 0:
                continue

            has_activity = True

            message += (
                f"💱 {currency}\n"
                f"🟢 Income: {symbol}{income_value:,.2f}\n"
                f"🔴 Expenses: {symbol}{expense_value:,.2f}\n"
                f"💰 Balance: {symbol}{wallets[currency]:,.2f}\n\n"
            )

        if not has_activity:
            message += (
                "No financial activity recorded yet.\n\n"
                "Try:\n"
                "/income 50000 NGN salary"
            )

        await update.message.reply_text(message)

    except Exception as error:
        print(f"Summary error: {error}")

        await update.message.reply_text(
            "❌ Unable to generate your financial summary. Please try again."
        )


# ============================================================
# 15. MARKET PLACEHOLDER
# ============================================================

async def market(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "📊 FINBOT MARKETS\n\n"
        "Market intelligence is being prepared.\n\n"
        "Coming next:\n\n"
        "🇪🇺 EUR/USD\n"
        "🇬🇧 GBP/USD\n"
        "🇺🇸 USD/NGN\n"
        "🇺🇸 USD/JPY\n"
        "🇬🇧 GBP/JPY\n\n"
        "FinBot will provide:\n"
        "• Live market prices\n"
        "• Market direction\n"
        "• RSI\n"
        "• EMA\n"
        "• MACD\n"
        "• Technical analysis\n"
        "• Market suggestions",
        reply_markup=back_button()
    )
def build_technical_report(pair, timeframe, analysis):

    timestamp = datetime.fromtimestamp(
        analysis["timestamp"] / 1000,
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    report = (
        f"📊 FINBOT TECHNICAL ANALYSIS\n\n"
        f"💱 Currency Pair: {pair}\n"
        f"⏱ Timeframe: {timeframe.title()}\n"
        f"🕒 Market Data: {timestamp}\n\n"

        f"📈 MARKET OVERVIEW\n\n"
        f"Price: {analysis['price']:.5f}\n"
        f"Trend: {analysis['trend']}\n"
        f"Market Structure: {analysis['market_structure']}\n\n"

        f"📉 MOVING AVERAGES\n\n"
        f"EMA 20: {analysis['ema20']:.5f}\n"
        f"EMA 50: {analysis['ema50']:.5f}\n"
        f"EMA 200: {analysis['ema200']:.5f}\n\n"

        f"📊 MOMENTUM INDICATORS\n\n"
        f"RSI (14): {analysis['rsi']:.2f}\n"
        f"Momentum: {analysis['momentum']}\n"
        f"MACD: {analysis['macd']:.6f}\n"
        f"MACD Signal: {analysis['signal']:.6f}\n"
        f"MACD Histogram: "
        f"{analysis['macd_histogram']:.6f}\n\n"

        f"🎯 RECENT PRICE LEVELS\n\n"
        f"50-Candle Low: {analysis['support']:.5f}\n"
        f"50-Candle High: {analysis['resistance']:.5f}\n\n"

        f"🌊 VOLATILITY\n\n"
        f"ATR (14): {analysis['atr']:.6f}\n\n"

        f"⚠️ This analysis uses historical market data "
        f"and is provided for educational purposes."
    )

    return report

# ============================================================
# 16. ANALYZE COMMAND PLACEHOLDER
# ============================================================
TELEGRAM_TEXT_LIMIT = 4000


async def send_long_message(message, text):


    while text:
        if len(text) <= TELEGRAM_TEXT_LIMIT:
            await message.reply_text(text)
            break

        split_at = text.rfind("\n", 0, TELEGRAM_TEXT_LIMIT)

        if split_at < TELEGRAM_TEXT_LIMIT // 2:
            split_at = TELEGRAM_TEXT_LIMIT

        chunk = text[:split_at].strip()
        text = text[split_at:].strip()

        await message.reply_text(chunk)






async def analyze(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not context.args:
        await update.message.reply_text(
            "🥇 FINBOT AI GOLD & FOREX ANALYSIS\n\n"
            "Available markets:\n"
            "/analyze XAUUSD\n"
            "/analyze EURUSD\n"
            "/analyze GBPUSD\n"
            "/analyze USDJPY\n\n"
            "Strategy: Scored Multi-Timeframe\n"
            "Timeframes: 15M | 1H | 4H\n"
            "Maximum planned risk: 1%\n"
            "Targets: 1:1, 1:2, 1:3\n\n"
            "Historical paper-trading analysis only."
        )
        return

    pair = context.args[0].upper().replace("/", "")
    if pair == "GOLD":
        pair = "XAUUSD"

    supported_pairs = {
        "XAUUSD", "EURUSD", "GBPUSD", "USDJPY"
    }

    if pair not in supported_pairs:
        await update.message.reply_text(
            "❌ Unsupported market.\n\n"
            "Use XAUUSD, EURUSD, GBPUSD or USDJPY."
        )
        return

    start_time = time.perf_counter()

    status = await update.message.reply_text(
        f"🔍 FINBOT ANALYZING {pair}\n\n"
        "📊 Loading historical candles...\n"
        "📈 Calculating multi-timeframe indicators...\n"
        "🎯 Evaluating BUY and SELL setups..."
    )

    try:
        # Historical data: never label this as live.
        end_date = (
            datetime.now(timezone.utc).date()
            - timedelta(days=1)
        )
        start_date = end_date - timedelta(days=70)

        async def fetch_frame(timeframe):
            return await get_historical_forex_candles(
                pair=pair,
                timeframe=timeframe,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat()
            )

        # Fetch sequentially to respect Massive rate limits.
        candles_15m = await fetch_frame("15m")
        candles_1h = await fetch_frame("1h")
        candles_4h = await fetch_frame("4h")

        # Calculate independent timeframe analyses.
        analysis_15m, analysis_1h, analysis_4h = (
            await asyncio.gather(
                asyncio.to_thread(
                    analyze_market, candles_15m
                ),
                asyncio.to_thread(
                    analyze_market, candles_1h
                ),
                asyncio.to_thread(
                    analyze_market, candles_4h
                )
            )
        )

        # Algorithm calculates levels, not Gemini.
        try:
            trade_setup = await asyncio.to_thread(
                generate_trade_setup,
                candles_15m=candles_15m,
                candles_1h=candles_1h,
                candles_4h=candles_4h,
                pair=pair,
                risk_percent=1.0,
                require_fresh=False
            )
        except TradeSetupError as error:
            logger.warning(
                "Trade setup rejected for %s: %s",
                pair, error
            )
            trade_setup = {
                "status": "NO TRADE",
                "reason": str(error),
                "checks": {}
            }

        technical_report = build_technical_report(
            pair, "15m", analysis_15m
        )

        checks = trade_setup.get("checks") or {}

        buy_score = trade_setup.get(
            "buy_score",
            checks.get("buy_score")
        )
        sell_score = trade_setup.get(
            "sell_score",
            checks.get("sell_score")
        )

        # New strategy scores up to 11 points.
        score_max = trade_setup.get(
            "maximum_score", 11
        )

        report = (
            "📊 FINBOT HISTORICAL MARKET REPORT\n\n"
            f"💱 Market: {pair}\n"
            "⏱ Timeframes: 15M | 1H | 4H\n\n"
            f"{technical_report}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🌍 MULTI-TIMEFRAME ANALYSIS\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📈 4H: {analysis_4h['trend']}\n"
            f"📈 1H: {analysis_1h['trend']}\n"
            f"📈 15M: {analysis_15m['trend']}\n\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🎯 FINBOT TRADE SETUP\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
        )

        if buy_score is not None and sell_score is not None:
            report += (
                "📊 CONFIRMATION SCORES\n"
                f"🟢 BUY: {buy_score}/{score_max}\n"
                f"🔴 SELL: {sell_score}/{score_max}\n"
                f"🎯 Required: "
                f"{checks.get('minimum_score', 6)} points "
                "plus entry confirmation\n\n"
            )

        if trade_setup.get("status") == "SETUP FOUND":
            direction = trade_setup["direction"]

            report += (
                "✅ QUALIFYING HISTORICAL SETUP\n\n"
                f"📌 Direction: {direction}\n"
                f"⚡ Setup: {trade_setup['setup_type']}\n"
                f"📊 Strategy: "
                f"{trade_setup['strategy']}\n\n"
                f"🎯 Entry: {trade_setup['entry']:.5f}\n"
                f"🛑 Stop Loss: "
                f"{trade_setup['stop_loss']:.5f}\n\n"
                f"💰 TP1 (1:1): "
                f"{trade_setup['take_profit_1']:.5f}\n"
                f"💰 TP2 (1:2): "
                f"{trade_setup['take_profit_2']:.5f}\n"
                f"💰 TP3 (1:3): "
                f"{trade_setup['take_profit_3']:.5f}\n\n"
                f"📊 RSI: {trade_setup['rsi']:.2f}\n"
                f"🛡 Risk setting: "
                f"{trade_setup['risk_percent']}%\n\n"
                f"📝 Explanation:\n"
                f"{trade_setup['reason']}\n"
            )

        else:
            report += (
                "🚫 NO QUALIFYING SETUP\n\n"
                f"📝 Reason:\n"
                f"{trade_setup.get('reason', 'Conditions not met.')}\n"
            )

        # Display individual scoring conditions.
        if checks:
            report += "\n📋 CONFIRMATION DETAILS\n"

            for key in ("buy_reasons", "sell_reasons"):
                reasons = checks.get(key, [])

                if reasons:
                    label = (
                        "BUY evidence"
                        if key == "buy_reasons"
                        else "SELL evidence"
                    )

                    report += f"\n{label}:\n"

                    for reason in reasons:
                        report += f"• {reason}\n"

            report += (
                "\nEntry triggers:\n"
                f"BUY: "
                f"{'Yes' if checks.get('buy_trigger') else 'No'}\n"
                f"SELL: "
                f"{'Yes' if checks.get('sell_trigger') else 'No'}\n"
            )

        elapsed = time.perf_counter() - start_time

        report += (
            "\n⚠️ HISTORICAL DEMONSTRATION\n"
            "Not a live trading signal. "
            "Educational and paper-trading use only.\n"
            "No trades are automatically executed.\n\n"
            f"⏱ Processing time: {elapsed:.1f}s"
        )

        if len(report) <= 4096:
            await status.edit_text(report)
        else:
            await status.edit_text(
                "✅ Analysis completed. Sending report..."
            )
            await send_long_message(
                update.message, report
            )

    except MarketDataError as error:
        logger.warning(
            "Historical market data error for %s: %s",
            pair, error
        )

        await status.edit_text(
            "⚠️ HISTORICAL MARKET DATA UNAVAILABLE\n\n"
            f"Market: {pair}\n"
            f"Details: {error}"
        )

    except TechnicalAnalysisError as error:
        logger.warning(
            "Technical analysis failed for %s: %s",
            pair, error
        )

        await status.edit_text(
            "❌ TECHNICAL ANALYSIS ERROR\n\n"
            f"Details: {error}"
        )

    except Exception as error:
        logger.exception(
            "Unexpected analysis error for %s",
            pair
        )

        await status.edit_text(
            "❌ FINBOT ANALYSIS ERROR\n\n"
            "An unexpected error occurred. "
            "Check the bot's terminal logs."
        )


# ============================================================
# 17. HELP
# ============================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "🤖 FINBOT AI COMMANDS\n\n"

        "🏠 Navigation\n"
        "/start - Start FinBot\n"
        "/menu - Main menu\n\n"
        "/profile - View your FinBot account\n"

        "💰 Finance\n"
        "/wallet - View wallets\n"
        "/income - Record income\n"
        "/expense - Record expense\n"
        "/history - Recent transactions\n"
        "/summary - Financial summary\n\n"

        "📈 Markets\n"
        "/market - Market dashboard\n"
        "/analyze EURUSD - Analyze currency pair\n\n"

        "💡 Examples\n\n"
        "/income 50000 NGN salary\n"
        "/expense 5000 NGN food\n"
        "/income 100 USD freelance\n"
        "/analyze EURUSD"
    )

    await update.message.reply_text(message)


# ============================================================
# 18. BUTTON HANDLER
# ============================================================
# ==========================================
# SAFE TELEGRAM MESSAGE EDITOR
# ==========================================

async def safe_edit_message(
    query,
    text,
    reply_markup=None
):
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup
        )

    except BadRequest as error:

        if "Message is not modified" in str(error):
            # Telegram already displays this content
            return

        # Raise other Telegram errors normally
        raise



async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if query is None:
        return

    await query.answer()
    user_id = query.from_user.id

    try:
        if query.data == "menu":
            await safe_edit_message(
                query,
                "🤖 FINBOT AI\n\nWhat would you like to do?",
                reply_markup=main_menu(),
            )

        elif query.data == "wallet":
            await register_user(query.from_user)
            wallets = await get_wallets(user_id)
            message = (
                "💰 FINBOT VIRTUAL WALLET\n\n"
                f"🇳🇬 NGN: ₦{wallets['NGN']:,.2f}\n"
                f"🇺🇸 USD: ${wallets['USD']:,.2f}\n"
                f"🇪🇺 EUR: €{wallets['EUR']:,.2f}\n"
                f"🇬🇧 GBP: £{wallets['GBP']:,.2f}\n\n"
                "Use:\n/income 50000 NGN salary\n"
                "/expense 5000 NGN food\n\n"
                "Virtual tracking wallet only; no real money is held."
            )
            await safe_edit_message(query, message, reply_markup=back_button())

        elif query.data == "markets":
            await safe_edit_message(
                query,
                "📊 MARKET TRENDS\n\n"
                "Explore forex analysis using /analyze EURUSD.\n\n"
                "Example pairs: EUR/USD, GBP/USD, USD/JPY, USD/NGN, GBP/JPY.\n"
                "Availability depends on your data provider.",
                reply_markup=back_button(),
            )

        elif query.data == "analyze":
            await safe_edit_message(
                query,
                "📈 FINBOT AI MARKET ANALYSIS\n\n"
                "Examples:\n/analyze EURUSD\n"
                "/analyze GBPUSD hour\n/analyze USDJPY day\n\n"
                "Indicators include RSI, MACD, EMA, ATR and recent price levels.\n"
                "Availability depends on your market-data provider.",
                reply_markup=back_button(),
            )

        elif query.data == "history":
            await register_user(query.from_user)
            transactions = await get_transactions(user_id, limit=5)
            if not transactions:
                message = "📜 TRANSACTIONS\n\nNo transactions recorded yet."
            else:
                message = "📜 RECENT TRANSACTIONS\n\n"
                for transaction in transactions:
                    currency = transaction["currency"]
                    symbol = SUPPORTED_CURRENCIES.get(currency, currency + " ")
                    kind = transaction["type"].lower()
                    icon = "🟢" if kind == "income" else "🔴"
                    amount = Decimal(transaction["amount_minor"]) / Decimal(100)
                    created = transaction.get("created_at")
                    date_text = created.strftime("%d %b %Y") if created else ""
                    message += (
                        f"{icon} {kind.title()} — {symbol}{amount:,.2f}\n"
                        f"{transaction.get('category', 'Other')}\n"
                        f"{date_text}\n\n"
                    )
            await safe_edit_message(query, message, reply_markup=back_button())

        elif query.data == "summary":
            await register_user(query.from_user)
            totals = await get_summary(user_id)
            wallets = await get_wallets(user_id)
            message = "📊 FINANCIAL SUMMARY\n\n"
            has_activity = False
            for currency, symbol in SUPPORTED_CURRENCIES.items():
                earned = totals[currency]["income"]
                spent = totals[currency]["expense"]
                if earned == 0 and spent == 0:
                    continue
                has_activity = True
                message += (
                    f"💱 {currency}\n"
                    f"🟢 Income: {symbol}{earned:,.2f}\n"
                    f"🔴 Expenses: {symbol}{spent:,.2f}\n"
                    f"💰 Balance: {symbol}{wallets[currency]:,.2f}\n\n"
                )
            if not has_activity:
                message += "No financial activity recorded yet."
            await safe_edit_message(query, message, reply_markup=back_button())

        elif query.data == "alerts":
            await safe_edit_message(
                query,
                "🔔 MARKET ALERTS\n\nMarket alerts are planned for a future update.",
                reply_markup=back_button(),
            )

        elif query.data == "premium":
            await safe_edit_message(
                query,
                "💎 FINBOT PREMIUM\n\n"
                "Premium features are planned for a future update, "
                "including advanced analysis and market alerts.",
                reply_markup=back_button(),
            )

        elif query.data == "help":
            await safe_edit_message(
                query,
                "❓ FINBOT HELP\n\n"
                "/income 50000 NGN salary\n"
                "/expense 5000 NGN food\n"
                "/analyze EURUSD\n"
                "/wallet\n/history\n/summary",
                reply_markup=back_button(),
            )

        else:
            await safe_edit_message(
                query,
                "⚠️ Unknown menu option. Please try again.",
                reply_markup=main_menu(),
            )

    except Exception:
        logger.exception("FinBot callback failed: %s", query.data)
        try:
            await safe_edit_message(
                query,
                "❌ Unable to load this information right now. Please try again.",
                reply_markup=back_button(),
            )
        except Exception:
            logger.exception("Could not display callback error")


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Exception while processing Telegram update",
        exc_info=(
            type(context.error),
            context.error,
            context.error.__traceback__
        ) if context.error else None
    )

    if isinstance(update, Update):

        if update.effective_chat:

            try:

                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=(
                        "⚠️ FinBot encountered an unexpected error.\n\n"
                        "Please try again shortly."
                    )
                )

            except Exception:
                logger.exception(
                    "Failed to notify user about error"
                )
async def database_startup(application):
    try:
        await connect_database()
        print("✅ FinBot is connected to MongoDB!")
    except Exception:
        print("❌ FinBot could not connect to MongoDB.")
        raise


async def database_shutdown(application):

    await close_market_client()

    await close_database()

    logger.info(
        "FinBot services shut down successfully."
    )
# ============================================================
# 19. CREATE APPLICATION
# ============================================================

def create_app():
    # ============================================================
    # FINBOT AI CHAT HANDLER
    # ============================================================

    async def ask_ai(
            update: Update,
            context: ContextTypes.DEFAULT_TYPE
    ):
        if not update.message:
            return

        # ==========================================
        # STEP 1: CHECK USER QUESTION
        # ==========================================

        if not context.args:
            await update.message.reply_text(
                "🤖 FINBOT AI ASSISTANT\n\n"
                "Ask me questions about:\n"
                "💰 Personal finance\n"
                "📊 Economics\n"
                "💱 Forex and gold\n"
                "📈 Technical analysis\n"
                "🏦 Banking and fintech\n"
                "🛡 Risk management\n\n"
                "Examples:\n"
                "/ask What is inflation?\n"
                "/ask Explain RSI and MACD\n"
                "/ask What causes currency depreciation?\n"
                "/ask How does compound interest work?"
            )
            return

        question = " ".join(context.args).strip()

        if not question:
            await update.message.reply_text(
                "❌ Please enter a question."
            )
            return

        if len(question) > 1000:
            await update.message.reply_text(
                "❌ Your question is too long.\n"
                "Maximum: 1000 characters."
            )
            return

        # ==========================================
        # STEP 2: INITIAL TELEGRAM MESSAGE
        # ==========================================

        status = await update.message.reply_text(
            "🧠 FINBOT AI\n\n"
            "Processing your question...\n"
            "🤖 Connecting to AI service..."
        )

        start_time = time.perf_counter()

        try:
            # ======================================
            # STEP 3: REQUEST AI RESPONSE
            # ======================================

            # Groq is primary; Gemini is fallback.
            # Provider retries belong in llm_service.py.
            #
            # This is a final safety deadline,
            # not a retry loop.

            answer = await asyncio.wait_for(
                ask_finbot(question),
                timeout=40
            )

            # ======================================
            # STEP 4: VALIDATE AI RESPONSE
            # ======================================

            if not isinstance(answer, str):
                raise ValueError(
                    "AI returned an invalid response type."
                )

            answer = answer.strip()

            if not answer:
                raise ValueError(
                    "AI returned an empty answer."
                )

            elapsed = time.perf_counter() - start_time

            logger.info(
                "FinBot /ask successful | time=%.2fs",
                elapsed
            )

            # ======================================
            # STEP 5: FORMAT TELEGRAM RESPONSE
            # ======================================

            response = (
                "🤖 FINBOT AI ASSISTANT\n\n"
                f"{answer}\n\n"
                f"⏱ Response time: {elapsed:.1f}s"
            )

            # ======================================
            # STEP 6: SEND TELEGRAM RESPONSE
            # ======================================

            if len(response) <= 4096:
                await status.edit_text(response)

            else:
                await status.edit_text(
                    "✅ FinBot has prepared your answer."
                )

                await send_long_message(
                    update.message,
                    response
                )

        # ==========================================
        # STEP 7: HANDLE TIMEOUT
        # ==========================================

        except asyncio.TimeoutError:

            logger.warning(
                "FinBot /ask exceeded the 40-second deadline."
            )

            await status.edit_text(
                "⏳ FINBOT AI TIMEOUT\n\n"
                "The AI service took too long to respond.\n\n"
                "Please try again shortly."
            )

        # ==========================================
        # STEP 8: HANDLE PROVIDER ERRORS
        # ==========================================

        except Exception as error:

            logger.exception(
                "FinBot /ask failed: %s",
                error
            )

            error_text = str(error).lower()

            if (
                    "api key" in error_text
                    or "not configured" in error_text
                    or "401" in error_text
                    or "403" in error_text
            ):
                message = (
                    "⚙️ FINBOT AI CONFIGURATION ERROR\n\n"
                    "The AI service could not authenticate.\n"
                    "Please contact the bot administrator."
                )

            elif (
                    "429" in error_text
                    or "resource_exhausted" in error_text
                    or "rate limit" in error_text
            ):
                message = (
                    "⚠️ AI REQUEST LIMIT REACHED\n\n"
                    "The AI provider is temporarily "
                    "limiting requests.\n\n"
                    "Please try again later."
                )

            elif (
                    "503" in error_text
                    or "502" in error_text
                    or "unavailable" in error_text
            ):
                message = (
                    "⚠️ AI SERVICE UNAVAILABLE\n\n"
                    "FinBot's AI providers are temporarily "
                    "unavailable.\n\n"
                    "Please try again shortly."
                )

            elif (
                    "timed out" in error_text
                    or "timeout" in error_text
            ):
                message = (
                    "⏳ AI RESPONSE TIMEOUT\n\n"
                    "The AI service did not respond "
                    "within the allowed time."
                )

            else:
                message = (
                    "❌ FINBOT AI ERROR\n\n"
                    "FinBot could not generate an answer.\n\n"
                    "The issue has been recorded in "
                    "the server logs."
                )

            await status.edit_text(message)

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(database_startup)
        .post_shutdown(database_shutdown)
        .build()
    )
    application.add_handler(
        CommandHandler("ask", ask_ai)
    )

    # Navigation
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("menu", menu)
    )

    # Wallet
    application.add_handler(
        CommandHandler("wallet", wallet)
    )

    application.add_handler(
        CommandHandler("income", income)
    )

    application.add_handler(
        CommandHandler("expense", expense)
    )

    application.add_handler(
        CommandHandler("profile", profile)
    )

    application.add_handler(
        CommandHandler("history", history)
    )

    application.add_handler(
        CommandHandler("summary", summary)
    )

    # Markets
    application.add_handler(
        CommandHandler("market", market)
    )

    application.add_handler(
        CommandHandler("analyze", analyze)
    )

    # Help
    application.add_handler(
        CommandHandler("help", help_command)
    )

    # Buttons
    application.add_handler(
        CallbackQueryHandler(button_handler)
    )

    application.add_error_handler(error_handler)
    return application


# ============================================================
# 20. RUN FIN BOT
# ============================================================

if __name__ == "__main__":

    print("=" * 40)
    print("🤖 FinBot AI is starting...")
    print("=" * 40)

    app = create_app()

    print("✅ FinBot AI is running.")

    app.run_polling()
