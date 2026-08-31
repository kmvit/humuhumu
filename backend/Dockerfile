FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# fonts-dejavu-core — кириллица для монограммы на иконке приложения
# (core/branding.py), когда у заведения ещё не загружен логотип.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# PIP_INDEX_URL — опциональное зеркало pip для серверов, где pypi.org недоступен
# (напр. РФ: https://pypi.tuna.tsinghua.edu.cn/simple). Пусто → обычный pypi.
ARG PIP_INDEX_URL=""
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt; \
    fi

COPY . .

EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
