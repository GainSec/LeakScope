#!/usr/bin/env sh
set -e

# Wait for Redis if specified
if [ -n "$CELERY_BROKER_URL" ]; then
  HOST=$(echo "$CELERY_BROKER_URL" | sed -E 's#redis://([^:/]+).*#\1#')
  PORT=$(echo "$CELERY_BROKER_URL" | sed -E 's#.*:([0-9]+)/.*#\1#')
  echo "Waiting for redis at $HOST:$PORT..."
  while ! nc -z "$HOST" "$PORT"; do
    sleep 1
  done
fi

python manage.py migrate --noinput

exec "$@"
