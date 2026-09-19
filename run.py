import os
from app import create_app

# Create Flask application instance
app = create_app(os.getenv("FLASK_ENV", "development"))

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8004))
    host = os.getenv("HOST", "0.0.0.0")
    debug = app.config.get("DEBUG", True)
    
    print(f"\n========================================================")
    print(f" FeatureApp Backend running at http://{host}:{port}/api/v1/")
    print(f" Health check: http://{host}:{port}/api/v1/health")
    print(f" Auth login:   http://{host}:{port}/api/v1/auth/login")
    print(f" Auth verify:  http://{host}:{port}/api/v1/auth/verify-code")
    print(f" Mode:         {app.config.get('ENV', 'development')}")
    print(f"========================================================\n")
    
    app.run(host=host, port=port, debug=debug)
