FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN pip install --no-cache-dir locust==2.32.10 \
    && useradd --system --uid 10001 --create-home locust
USER locust
WORKDIR /load
ENTRYPOINT ["locust"]
