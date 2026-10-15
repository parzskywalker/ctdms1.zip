"""Gunicorn settings (override with GUNICORN_* env vars)."""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 8)))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
# Only these proxies may set X-Forwarded-For (the client IP written to the audit log).
# Behind the bundled Caddy (docker network) use the network range, e.g. FORWARDED_ALLOW_IPS=172.16.0.0/12.
# "*" would let any client spoof its IP when port 8000 is reachable directly.
forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
