FROM python:3.11-slim

WORKDIR /app

# Install system utilities (curl needed for docker healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and dataset
COPY config.yaml .
COPY data/ data/
COPY engine/ engine/
COPY grid/ grid/
COPY server/ server/

EXPOSE 8000

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
