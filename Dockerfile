FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m appuser && chown -R appuser:appuser /app

USER appuser

COPY --chown=appuser:appuser . .

ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

EXPOSE 8000 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["supervisord", "-c", "supervisord.conf"]
