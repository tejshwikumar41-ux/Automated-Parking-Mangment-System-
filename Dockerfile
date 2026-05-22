# Builder Stage
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Runner Stage
FROM python:3.12-slim AS runner

WORKDIR /app

# Copy installed python packages from builder
COPY --from=builder /root/.local /root/.local
COPY . /app

# Create a data directory for SQLite persistence
RUN mkdir -p /data

# Ensure local bin is on PATH (for uvicorn etc)
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONUNBUFFERED=1
ENV DB_FILE=/data/parking.db

EXPOSE 8000

# Run FastAPI server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
