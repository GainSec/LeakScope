FROM python:3.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CELERY_BROKER_URL=redis://redis:6379/0

WORKDIR /app

COPY requirements.txt /app/
# Install runtime deps (git for gitpython, netcat for health/wait checks) then Python deps
RUN apt-get update \
    && apt-get install -y --no-install-recommends git netcat-openbsd \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY . /app

# Entrypoint waits for dependencies and applies migrations
ENTRYPOINT ["/app/entrypoint.sh"]

# Default command runs Django dev server (can override with gunicorn)
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
