# Imagem do var-system.
# ML (ultralytics/torch) e opcional via build arg para nao inflar a imagem:
#   docker build --build-arg INSTALL_ML=true -f docker/Dockerfile -t var-system .
FROM python:3.12-slim

ARG INSTALL_ML=false
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Dependencias de sistema:
#  - ffmpeg: ingestao ao vivo dentro do container
#  - libgl1 / libglib2.0-0: runtime do OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencias Python base (sem ML). opencv-python-headless evita libs de GUI.
RUN pip install \
        "numpy>=1.26" \
        "opencv-python-headless>=4.9" \
        "pyyaml>=6.0" \
        "fastapi>=0.110" \
        "uvicorn[standard]>=0.29" \
        "pydantic>=2.6" \
        "kafka-python>=2.0"

# ML opcional (CPU). Pesado (~1GB+): so quando INSTALL_ML=true.
RUN if [ "$INSTALL_ML" = "true" ]; then \
        pip install "torch>=2.2" "torchvision>=0.17" "ultralytics>=8.2"; \
    fi

COPY var/ ./var/
COPY config.yaml ./config.yaml

EXPOSE 8000

# Por padrao sobe a API de decisao. Override via 'command' no compose.
CMD ["python", "-m", "var.cli", "api"]
