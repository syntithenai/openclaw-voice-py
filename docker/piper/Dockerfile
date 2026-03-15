FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn piper-tts

COPY app.py ./app.py

RUN mkdir -p /root/.local/share/piper

EXPOSE 10001
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10001"]
