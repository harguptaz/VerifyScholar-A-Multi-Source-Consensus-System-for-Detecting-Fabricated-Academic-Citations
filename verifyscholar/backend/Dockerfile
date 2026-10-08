FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# LibreOffice (headless) lets the app convert DOCX uploads to PDF. It adds ~500 MB to the image.
# Build without it:  docker compose build --build-arg INSTALL_LIBREOFFICE=false backend
ARG INSTALL_LIBREOFFICE=true
RUN if [ "$INSTALL_LIBREOFFICE" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-dejavu-core \
      && rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
COPY requirements.txt requirements-ml.txt ./
RUN pip install -r requirements.txt

# Optional meaning-based similarity (adds torch + a ~90 MB model download on first use).
# Enable with:  docker compose build --build-arg INSTALL_ML=true backend
ARG INSTALL_ML=false
RUN if [ "$INSTALL_ML" = "true" ]; then \
      pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements-ml.txt; \
    fi

COPY app ./app
COPY evaluation ./evaluation
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
