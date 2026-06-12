# NHB disclosure-experiments platform — container image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install -r server/requirements.txt

COPY . .

# Data is written under /app/data by default; mount a volume there for persistence.
ENV DATA_DIR=/app/data
EXPOSE 8000
WORKDIR /app/server
# Shell form so $PORT (set by most PaaS) is expanded; falls back to 8000 locally.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
