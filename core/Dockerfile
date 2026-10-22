FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install ".[hardware]"

RUN useradd --create-home --uid 1000 hidro \
    && mkdir -p /data \
    && chown -R hidro:hidro /app /data

USER hidro
ENV HIDRO_CONFIG=/app/config/hidro.yaml \
    HIDRO_DATA_DIR=/data

ENTRYPOINT ["hidro"]
CMD ["run"]
