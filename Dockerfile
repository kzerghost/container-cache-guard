FROM python:3.12-alpine
ARG APP_VERSION=0.2.2
LABEL org.opencontainers.image.title="Container Cache Guard" \
      org.opencontainers.image.description="On-demand repair of a specific Synology Container Manager name-cache mismatch" \
      org.opencontainers.image.source="https://github.com/kzerghost/container-cache-guard" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${APP_VERSION}"
WORKDIR /app
COPY guard.py engine.py service.py index.html LICENSE ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "service.py"]
