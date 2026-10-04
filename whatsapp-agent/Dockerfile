# Production Dockerfile for WhatsApp AI Agent
FROM python:3.12-slim as base

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create virtualenv and install python dependencies
RUN python -m venv /venv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Run uvicorn server with 2 worker processes
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
