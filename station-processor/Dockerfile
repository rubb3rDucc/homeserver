FROM python:3.12-slim

# ffmpeg + ffprobe are the only runtime dependencies; the processor itself
# uses nothing outside the Python standard library.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY process.py /app/process.py

# Unbuffered so logs show up live in `docker logs`.
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "/app/process.py"]
