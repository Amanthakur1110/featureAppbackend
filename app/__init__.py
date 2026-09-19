import os
import logging
from flask import Flask, jsonify
from flask_cors import CORS

from app.config import config_by_name
from app.db import init_db
from app.routes import register_routes
from app.utils.response import error_response


def create_app(config_name=None):
    """
    Application factory for FeatureApp Backend.
    Sets up config, database, routes, CORS, and error handlers.
    """
    if config_name is None:
        config_name = os.getenv("FLASK_ENV", "development").lower()

    app = Flask(__name__)

    # Load configuration
    config_class = config_by_name.get(config_name, config_by_name["default"])
    app.config.from_object(config_class)

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if app.config["DEBUG"] else logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    )
    logger = logging.getLogger("app")
    logger.info(f"Initializing FeatureApp Backend in [{config_name}] mode")

    # Enable Cross-Origin Resource Sharing (CORS)
    # Must cover both /api/* (API) and /feature/* (static WebView served files)
    CORS(app, resources={r"/*": {"origins": "*"}})

    # Initialize MongoDB connection & indexes
    init_db(app)

    # Register all modular blueprints under /api/v1
    register_routes(app, base_prefix="/api/v1")

    # Register error handlers for standardized API envelopes
    @app.errorhandler(400)
    def bad_request(e):
        return error_response(message="Bad Request: " + str(e), status_code=400)

    @app.errorhandler(404)
    def not_found(e):
        return error_response(message="Endpoint not found", status_code=404)

    @app.errorhandler(405)
    def method_not_allowed(e):
        return error_response(message="HTTP method not allowed for this endpoint", status_code=405)

    @app.errorhandler(500)
    def internal_server_error(e):
        logger.error(f"Internal server error: {e}", exc_info=True)
        return error_response(message="Internal server error", status_code=500)

    return app
