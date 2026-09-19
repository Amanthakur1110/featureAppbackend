import os
from datetime import timedelta
from dotenv import load_dotenv

# Load .env file
load_dotenv()


class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv("SECRET_KEY", "default-flask-secret-key-change-in-prod")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "default-jwt-secret-key-change-in-prod")
    JWT_ACCESS_TOKEN_EXPIRES_DAYS = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRES_DAYS", "30"))
    
    # MongoDB settings
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/featureapp")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "featureapp")
    
    # OTP settings
    OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))
    
    # Server settings
    PORT = int(os.getenv("PORT", "8004"))
    HOST = os.getenv("HOST", "0.0.0.0")
    
    DEBUG = False
    TESTING = False


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True


class ProductionConfig(Config):
    """Production configuration."""
    DEBUG = False


class TestingConfig(Config):
    """Testing configuration."""
    TESTING = True
    DEBUG = True
    MONGO_DB_NAME = "featureapp_test"
    MONGO_URI = os.getenv("TEST_MONGO_URI", "mongodb://localhost:27017/featureapp_test")


config_by_name = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig
}
