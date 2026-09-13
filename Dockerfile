FROM python:3.12-slim

WORKDIR /app

# ffmpeg вытаскивает звук из голосовых (.ogg) и кружков (.mp4)
# для транскрибации в !цитата. HF_HUB_CACHE — чтобы модель whisper
# качалась один раз и жила в volume (см. docker-compose.yml).
ENV HF_HUB_CACHE=/app/whisper-cache
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
