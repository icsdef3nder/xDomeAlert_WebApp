"""
xDome Alert Web App - Entry point.

Run with:
    python run.py

In production, use gunicorn behind nginx:
    gunicorn -w 4 -b 127.0.0.1:5000 'app:create_app()'
"""
import os
import logging

from app import create_app

# Configure structured logging early so startup events are captured for SIEM.
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)

app = create_app()

if __name__ == "__main__":
    # Bind to localhost by default. Production deployments should use a WSGI
    # server (gunicorn/uWSGI) behind nginx with TLS termination.
    host = os.environ.get("FLASK_HOST", "127.0.0.1")
    port = int(os.environ.get("FLASK_PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"

    app.run(host=host, port=port, debug=debug)
