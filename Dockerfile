FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies globally
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create a data directory for SQLite persistence and make it writable by any user
RUN mkdir -p /data && chmod 777 /data

# Default environment variables
ENV DB_FILE=/data/parking.db
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# Start server using the dynamic PORT env variable passed by Render
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]

