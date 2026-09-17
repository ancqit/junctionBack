FROM python:3.12-slim

# Install system dependencies including updated CA certs, OpenSSL, and ffmpeg
# (H.264 lean playback for shorts — Instagram/YouTube-style progressive MP4).
RUN apt-get update && apt-get install -y \
    build-essential \
    libssl-dev \
    ca-certificates \
    fonts-dejavu-core \
    fonts-noto-core \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

