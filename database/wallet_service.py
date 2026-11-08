from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from pymongo import ReturnDocument

from database.connection import (
    users_collection,
    wallets_collection,
    transactions_collection,
)

SUPPORTED_CURRENCIES = ("NGN", "USD", "EUR", "GBP")


def to_minor_units(amount):
    """Convert a positive monetary amount to integer minor units."""

    try:
        value = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid amount.")

    if not value.is_finite() or value <= 0:
        raise ValueError("Amount must be greater than zero.")

    rounded = value.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )

    if rounded != value:
        raise ValueError("Amount cannot exceed two decimal places.")

    return int(rounded * 100)


def from_minor_units(amount_minor):
    return Decimal(amount_minor) / Decimal(100)


def validate_currency(currency):
    currency = currency.upper()

    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("Unsupported currency.")

    return currency


async def register_user(telegram_user):
    """Create or update a Telegram user's profile."""

    telegram_id = telegram_user.id

    await users_collection.update_one(
        {"telegram_id": telegram_id},
        {
            "$set": {
                "first_name": telegram_user.first_name,
                "username": telegram_user.username,
                "last_active": datetime.now(timezone.utc),
            },
            "$setOnInsert": {
                "created_at": datetime.now(timezone.utc),
                "is_premium": False,
            },
        },
        upsert=True,
    )

    for currency in SUPPORTED_CURRENCIES:
        await wallets_collection.update_one(
            {
                "telegram_id": telegram_id,
                "currency": currency,
            },
            {
                "$setOnInsert": {
                    "balance_minor": 0,
                }
            },
            upsert=True,
        )


async def get_wallets(telegram_id):
    """Return all currency balances."""

    result = {}

    for currency in SUPPORTED_CURRENCIES:
        wallet = await wallets_collection.find_one(
            {
                "telegram_id": telegram_id,
                "currency": currency,
            }
        )

        balance_minor = (
            wallet.get("balance_minor", 0)
            if wallet else 0
        )

        result[currency] = from_minor_units(balance_minor)

    return result


async def record_transaction(
    telegram_id,
    transaction_type,
    amount,
    currency,
    category,
):
    """Update a virtual balance and save its transaction."""

    currency = validate_currency(currency)
    amount_minor = to_minor_units(amount)

    if transaction_type not in ("income", "expense"):
        raise ValueError("Invalid transaction type.")

    if not category or not str(category).strip():
        raise ValueError("Transaction category is required.")

    wallet_filter = {
        "telegram_id": telegram_id,
        "currency": currency,
    }

    # The balance update and history insert should be committed
    # together so they cannot disagree.
    from database.connection import client

    async with client.start_session() as session:
        async with await session.start_transaction():

            if transaction_type == "income":

                wallet = await wallets_collection.find_one_and_update(
                    wallet_filter,
                    {"$inc": {"balance_minor": amount_minor}},
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )

            else:

                wallet = await wallets_collection.find_one_and_update(
                    {
                        **wallet_filter,
                        "balance_minor": {"$gte": amount_minor},
                    },
                    {"$inc": {"balance_minor": -amount_minor}},
                    return_document=ReturnDocument.AFTER,
                    session=session,
                )

            if wallet is None:
                raise ValueError(
                    "Wallet not found or insufficient balance."
                )

            transaction = {
                "telegram_id": telegram_id,
                "type": transaction_type,
                "amount_minor": amount_minor,
                "currency": currency,
                "category": str(category).strip(),
                "created_at": datetime.now(timezone.utc),
            }

            await transactions_collection.insert_one(
                transaction,
                session=session,
            )

            return {
                "currency": currency,
                "amount": from_minor_units(amount_minor),
                "balance": from_minor_units(
                    wallet["balance_minor"]
                ),
            }


async def get_transactions(telegram_id, limit=10):
    """Retrieve a user's recent transactions."""

    cursor = (
        transactions_collection.find(
            {"telegram_id": telegram_id}
        )
        .sort("created_at", -1)
        .limit(limit)
    )

    return await cursor.to_list(length=limit)


async def get_summary(telegram_id):
    """Calculate income and expenses per currency."""

    transactions = await transactions_collection.find(
        {"telegram_id": telegram_id}
    ).to_list(length=None)

    summary = {
        currency: {
            "income": Decimal("0.00"),
            "expense": Decimal("0.00"),
        }
        for currency in SUPPORTED_CURRENCIES
    }

    for transaction in transactions:

        currency = transaction["currency"]
        transaction_type = transaction["type"]

        if currency not in summary:
            continue

        if transaction_type not in ("income", "expense"):
            continue

        summary[currency][transaction_type] += from_minor_units(
            transaction["amount_minor"]
        )

    return summary