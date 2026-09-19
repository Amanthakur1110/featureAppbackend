import logging
import time
from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
from flask import current_app, g

logger = logging.getLogger("mongo_db")


class MongoManager:
    def __init__(self, app=None):
        self.client = None
        self.db = None
        if app is not None:
            self.init_app(app)

    def init_app(self, app):
        """Initializes MongoDB connection with Flask app context."""
        uri = app.config.get("MONGO_URI", "mongodb://localhost:27017/featureapp")
        db_name = app.config.get("MONGO_DB_NAME", "featureapp")
        
        logger.info(f"Connecting to MongoDB at {uri} (Database: {db_name})...")
        
        # Initialize client with a 5 second server selection timeout
        self.client = MongoClient(
            uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            maxPoolSize=50
        )
        self.db = self.client[db_name]

        # Ensure indexes in background
        try:
            self._init_indexes()
            logger.info("MongoDB indexes verified successfully.")
        except Exception as e:
            logger.warning(f"Could not initialize indexes immediately (will retry on first request): {e}")

    def _init_indexes(self):
        """Initializes database indexes."""
        if self.db is None:
            return

        # Users collection indexes
        self.db.users.create_index([("email", ASCENDING)], unique=True, background=True)

        # Verification tokens / OTP sessions indexes
        self.db.verification_tokens.create_index([("token", ASCENDING)], unique=True, background=True)
        self.db.verification_tokens.create_index([("email", ASCENDING)], background=True)
        # MongoDB TTL index to automatically purge expired records
        self.db.verification_tokens.create_index(
            [("expires_at", ASCENDING)],
            expireAfterSeconds=0,
            background=True
        )

    def ping(self) -> tuple[bool, str]:
        """Pings the MongoDB server to verify health."""
        if not self.client:
            return False, "MongoClient not initialized"
        try:
            self.client.admin.command('ping')
            return True, "MongoDB is reachable"
        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            return False, f"MongoDB connection failure: {str(e)}"
        except Exception as e:
            return False, f"MongoDB error: {str(e)}"

    def get_database(self):
        """Returns the active MongoDB database object."""
        if self.db is None:
            # Fallback initialization from current_app if needed
            uri = current_app.config.get("MONGO_URI")
            db_name = current_app.config.get("MONGO_DB_NAME")
            self.client = MongoClient(uri, serverSelectionTimeoutMS=5000)
            self.db = self.client[db_name]
        return self.db

    def close(self):
        """Closes the MongoDB connection."""
        if self.client:
            self.client.close()
            logger.info("MongoDB connection closed.")


mongo_manager = MongoManager()


def init_db(app):
    """Convenience helper to initialize DB with app."""
    mongo_manager.init_app(app)


def get_db():
    """Returns database instance for current app."""
    return mongo_manager.get_database()


def close_db(e=None):
    """Teardown handler."""
    # Mongo connections in PyMongo are pooled; we don't close per-request,
    # but we can provide this hook if needed.
    pass
