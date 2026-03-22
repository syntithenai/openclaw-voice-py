FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    swig \
    curl \
    ca-certificates \
    libasound2 \
    libasound2-dev \
    libpulse0 \
    libpulse-dev \
    libportaudio2 \
    portaudio19-dev \
    libsndfile1 \
    alsa-utils \
    pulseaudio-utils \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-optional.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -r requirements-optional.txt

COPY orchestrator ./orchestrator

RUN mkdir -p /music /app/workspace/.media /app/workspace/playlists

CMD ["python", "-m", "orchestrator.main"]
