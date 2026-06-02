FROM python:3.11-slim

# Install ffmpeg for audio+video merging
RUN apt-get update && \
    apt-get install -y ffmpeg --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY server.py .
COPY public/ ./public/

# Cloud Run sets PORT env variable
ENV PORT=8080

EXPOSE 8080

CMD ["python", "server.py"]
