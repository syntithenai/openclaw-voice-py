FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./app.py

# Create directory for piper models
RUN mkdir -p /root/.local/share/piper

EXPOSE 10001
ENTRYPOINT ["python", "-m"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "10001"]
