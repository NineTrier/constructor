import multiprocessing
import os

bind = "0.0.0.0:8000"
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
timeout = int(os.getenv("GUNICORN_TIMEOUT", 120))
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
