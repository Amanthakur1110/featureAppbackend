from flask import Blueprint
from app.routes.auth_routes import auth_bp
from app.routes.user_routes import user_bp
from app.utils.response import success_response, error_response
from app.db import mongo_manager

# List of all feature route blueprints to be registered
# To add a new feature route:
# 1. Create a new file in app/routes/ (e.g., product_routes.py)
# 2. Import its blueprint here and add it to ALL_BLUEPRINTS
ALL_BLUEPRINTS = [
    auth_bp,
    user_bp,
]


def register_routes(app, base_prefix: str = "/api/v1"):
    """
    Registers all route blueprints under the specified base prefix (e.g. /api/v1).
    Also attaches a health check route on {base_prefix}/health.
    """
    # Register feature blueprints
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp, url_prefix=f"{base_prefix}{bp.url_prefix}")

    # Health check route
    @app.route(f"{base_prefix}/health", methods=["GET"])
    def health_check():
        mongo_ok, mongo_status = mongo_manager.ping()
        status_code = 200 if mongo_ok else 503
        data = {
            "status": "UP" if mongo_ok else "DEGRADED",
            "database": {
                "type": "MongoDB",
                "status": mongo_status,
                "healthy": mongo_ok
            },
            "service": "featureAppBackend",
            "version": "1.0.0"
        }
        if mongo_ok:
            return success_response(data=data, message="System is healthy", status_code=status_code)
        else:
            return error_response(data=data, message=f"Service degraded: {mongo_status}", status_code=status_code)
