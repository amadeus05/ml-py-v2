# Базовый образ с Python 3.11 (slim-версия для минимального размера)
FROM python:3.11-slim

# Установка системных зависимостей (нужны для некоторых Python-пакетов)
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# Создаем рабочую директорию
WORKDIR /app

# Сначала копируем только requirements.txt, чтобы Docker закэшировал установку пакетов
COPY requirements.txt .

# Настройка окружения
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта в контейнер
COPY . .

# Команда по умолчанию (будет переопределена в docker-compose)
CMD ["python", "live.py"]
