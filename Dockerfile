FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LEAKSCOPE_DATA_DIR=/data/app \
    LEAKSCOPE_EXPORT_DIR=/data/artifacts/exports \
    LEAKSCOPE_STATIC_ROOT=/app/staticfiles

WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --require-hashes -r requirements.txt
COPY . ./
RUN chmod 755 /app/entrypoint.sh \
    && useradd --uid 501 --gid 20 --create-home --home-dir /home/leakscope leakscope \
    && LEAKSCOPE_SECRET_KEY=static-build-only LEAKSCOPE_DATA_DIR=/tmp/leakscope-static python manage.py collectstatic --noinput \
    && rm -rf /tmp/leakscope-static \
    && mkdir -p /data/app /data/artifacts /data/beat \
    && chown -R 501:20 /data /home/leakscope
USER 501:20
EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "leakscope.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-"]
