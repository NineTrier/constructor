FROM python:3.8-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/taskmanager:/app

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends wget gnupg ca-certificates \
    && wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release && echo $VERSION_CODENAME)-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libffi-dev \
        libxml2-dev \
        libxslt1-dev \
        libjpeg62-turbo-dev \
        zlib1g-dev \
        libfreetype6-dev \
        libmagic-dev \
        netcat-openbsd \
        libsm6 \
        libxext6 \
        libxrender-dev \
        poppler-utils \
        antiword \
        unrtf \
        tesseract-ocr \
        qpdf \
        ghostscript \
        default-libmysqlclient-dev \
        postgresql-client-16 \
        postgresql-client-17 \
        libaio1 \
    && apt-get purge -y --auto-remove wget gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "pip<24.1" && \
    pip install --no-cache-dir --default-timeout=120 --retries 10 -r /tmp/requirements.txt

RUN sed -i 's/\r$//' /app/docker/entrypoint.sh && chmod +x /app/docker/entrypoint.sh

COPY . /app

RUN mkdir -p /app/staticfiles /app/media

ENV DJANGO_SETTINGS_MODULE=taskmanager.settings \
    DJANGO_FIXTURE_PATH=/app/backup.json \
    DJANGO_AUTO_IMPORT_DUMP=1 \
    DJANGO_SUPERUSER_USERNAME=admin \
    DJANGO_SUPERUSER_PASSWORD=admin \
    DJANGO_SUPERUSER_EMAIL=admin@example.com

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "--config", "docker/gunicorn.conf.py", "taskmanager.wsgi:application"]
