ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

# 构建源：默认官方；中国网络在 deploy.env 里填 APT_MIRROR / PIP_INDEX_URL
ARG APT_MIRROR=
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# edge-tts 出 mp3，容器里转 Ogg Opus
RUN if [ -n "$APT_MIRROR" ]; then \
        if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
            sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
        else \
            sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list; \
        fi; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -i "$PIP_INDEX_URL" -r requirements.txt

COPY app ./app
COPY tools ./tools

EXPOSE 8100
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
