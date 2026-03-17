FROM rocm/pytorch:latest

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./app.py

ENV PYANNOTE_MODEL_ID=pyannote/speaker-diarization-3.1
ENV PYANNOTE_BACKEND_PREFERENCE=auto
ENV PYANNOTE_CPU_FALLBACK=true
ENV HF_HOME=/models

EXPOSE 10002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10002"]
