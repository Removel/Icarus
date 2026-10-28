FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# onnxruntime (FastEmbed) needs libgomp at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install the vendored SDK from this checkout, not a published wheel.
WORKDIR /app/packages
COPY pyproject.toml poetry.lock README.md LICENSE ./
COPY mem0 ./mem0
RUN pip install --no-cache-dir .

WORKDIR /app
COPY server ./

RUN useradd --uid 10001 --create-home app \
    && mkdir -p /data/history /data/models \
    && chown -R app:app /app /data
USER app

ENV HISTORY_DB_PATH=/data/history/history.db \
    FASTEMBED_CACHE_PATH=/data/models

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/docs', timeout=3)"

CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
