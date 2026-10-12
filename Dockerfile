FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The Whisper model is not baked into the image. Download it once into
# WHISPER_MODEL_DIR (models/whisper -> /app/models/whisper, a Compose volume):
#   docker compose exec api python -m app.voice.download_model

COPY app ./app
COPY ui ./ui
COPY data ./data

# One worker: the Excel store keeps a single in-process copy of the workbook.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
