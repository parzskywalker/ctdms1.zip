# CTDMS - production image (FastAPI + Gunicorn/Uvicorn workers)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client tzdata curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements.lock.txt ./
RUN pip install -r requirements.lock.txt

COPY . .
RUN useradd --create-home --uid 1000 ctdms \
 && mkdir -p /app/data/uploads /app/data/backups /app/data/logs \
 && chown -R ctdms:ctdms /app/data /app/sample_data \
 && chmod +x deploy/entrypoint.sh deploy/backup.sh

USER ctdms
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fsS http://localhost:8000/health || exit 1
ENTRYPOINT ["deploy/entrypoint.sh"]
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-c", "deploy/gunicorn.conf.py"]
