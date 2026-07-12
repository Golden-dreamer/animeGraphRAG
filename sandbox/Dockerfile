FROM python:3.12-slim

# Инструменты для тестов и отладки
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Зависимости проекта (для импорта graphrag.py, db.py и т.д.)
COPY requirements.txt /tmp/reqs.txt
RUN pip install --no-cache-dir -r /tmp/reqs.txt pytest

# Код монтируется через volume — COPY не нужен
CMD ["sleep", "infinity"]