FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/apps/server/backend:/app/apps/server/shared

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY apps/server/backend/requirements.txt /tmp/backend-requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /tmp/backend-requirements.txt

COPY apps/server/backend /app/apps/server/backend
COPY apps/server/worker /app/apps/server/worker
COPY apps/server/shared /app/apps/server/shared

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /app/data \
    && chown -R app:app /app

USER app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
