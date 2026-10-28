FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY scripts ./scripts
# Defaults for identity/server; connection fields are overridden at runtime.
COPY settings.example.json ./settings.json
RUN useradd --uid 10001 --create-home app
USER app
ENV RELA_MEM_CONFIG=/app/settings.json
EXPOSE 8767
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8767/healthz', timeout=8)"
CMD ["python", "scripts/manage.py", "serve"]
