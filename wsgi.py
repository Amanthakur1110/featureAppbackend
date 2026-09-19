import os
from app import create_app

# WSGI application callable for Gunicorn / uWSGI
app = create_app(os.getenv("FLASK_ENV", "production"))

if __name__ == "__main__":
    app.run()
