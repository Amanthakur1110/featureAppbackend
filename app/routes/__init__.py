from flask import Blueprint
from app.routes.auth_routes import auth_bp
from app.routes.user_routes import user_bp
from app.routes.feature_routes import feature_bp, static_feature_bp
from app.utils.response import success_response, error_response
from app.db import mongo_manager

# List of all feature route blueprints to be registered
ALL_BLUEPRINTS = [
    auth_bp,
    user_bp,
    feature_bp,
]


def register_routes(app, base_prefix: str = "/api/v1"):
    """
    Registers all route blueprints under the specified base prefix (e.g. /api/v1).
    Also attaches a health check route on {base_prefix}/health.
    Registers static_feature_bp without prefix for clean /feature/get/<uid> access.
    """
    # Register API blueprints
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp, url_prefix=f"{base_prefix}{bp.url_prefix}")

    # Register static feature serving blueprint (direct /feature/get/<uid>)
    app.register_blueprint(static_feature_bp)

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
