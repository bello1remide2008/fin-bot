import os
import certifi

from pathlib import Path
from dotenv import load_dotenv
from pymongo import AsyncMongoClient, ASCENDING, DESCENDING


# ==========================================
# ENVIRONMENT CONFIGURATION
# ==========================================

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

MONGO_URI = os.getenv("MONGO_URI")

DB_NAME = os.getenv(
    "MONGO_DB_NAME",
    "finbot_ai"
)

if not MONGO_URI:
    raise ValueError(
        "MONGO_URI is missing from .env"
    )


# ==========================================
# MONGODB CLIENT
# ==========================================

client = AsyncMongoClient(
    MONGO_URI,

    tls=True,

    tlsCAFile=certifi.where(),

    serverSelectionTimeoutMS=20000,

    connectTimeoutMS=20000,

    socketTimeoutMS=20000,
)


# ==========================================
# DATABASE COLLECTIONS
# ==========================================

db = client[DB_NAME]

users_collection = db["users"]

wallets_collection = db["wallets"]

transactions_collection = db["transactions"]


# ==========================================
# CONNECT DATABASE
# ==========================================

async def connect_database():

    print("🔄 Connecting to MongoDB Atlas...")

    await client.admin.command("ping")

    print("✅ MongoDB server connection successful!")

    # Create unique user index
    await users_collection.create_index(
        [("telegram_id", ASCENDING)],
        unique=True
    )

    # Create unique wallet index
    await wallets_collection.create_index(
        [
            ("telegram_id", ASCENDING),
            ("currency", ASCENDING)
        ],
        unique=True
    )

    # Create transaction index
    await transactions_collection.create_index(
        [
            ("telegram_id", ASCENDING),
            ("created_at", DESCENDING)
        ],
        name="user_recent_transactions"
    )

    print("✅ MongoDB indexes initialized!")
    print(f"📁 Database: {DB_NAME}")


# ==========================================
# CLOSE DATABASE
# ==========================================

async def close_database():

    await client.close()

    print("🔒 MongoDB connection closed.")