FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu \
    "torch==2.15.0+cpu" \
    && pip install --no-cache-dir -r /tmp/requirements.txt

RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && mkdir -p /var/lib/vaultrag/documents /var/cache/vaultrag \
    && chown -R app:app /app /var/lib/vaultrag /var/cache/vaultrag
COPY --chown=app:app backend/app /app/app
COPY --chown=app:app backend/alembic /app/alembic
COPY --chown=app:app backend/alembic.ini /app/alembic.ini
ENV PYTHONPATH=/app HF_HOME=/var/cache/vaultrag
USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
