FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml uv.lock* ./
RUN pip install --no-cache-dir uv && (uv sync --frozen --no-dev || uv sync --no-dev)

COPY app ./app
COPY static ./static

RUN mkdir -p /app/data
EXPOSE 8000

CMD ["sh", "-c", ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
