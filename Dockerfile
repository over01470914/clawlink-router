# ---------- build stage ----------
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml ./
COPY clawlink_router/ ./clawlink_router/
COPY run.py ./

RUN pip install --no-cache-dir --prefix=/install .

# ---------- runtime stage ----------
FROM python:3.12-slim

LABEL maintainer="ClawLink Team"
LABEL version="1.0.0"

WORKDIR /app

COPY --from=builder /install /usr/local
COPY --from=builder /build/run.py .
COPY --from=builder /build/clawlink_router/ ./clawlink_router/

EXPOSE 8420

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8420/health')" || exit 1

CMD ["python", "run.py"]
