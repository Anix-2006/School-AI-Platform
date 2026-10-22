FROM python:3.11-slim

WORKDIR /app

# gTTS replies are MP3. WhatsApp voice notes are OGG/Opus, so ffmpeg must be present.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG WHISPER_MODEL=tiny
ENV HF_HOME=/opt/huggingface
# Bake the default speech model into the image instead of downloading it
# during the first live request after a Render restart.
RUN python -c "from faster_whisper import download_model; download_model('${WHISPER_MODEL}')"

COPY app ./app
COPY ui ./ui
COPY data ./data

# One worker: the Excel store keeps a single in-process copy of the workbook.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
