FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY data/fixtures ./data/fixtures
COPY data/reference/sk01 ./data/reference/sk01
COPY data/techniques.json ./data/techniques.json

RUN pip install --no-cache-dir . && mkdir -p /app/data/knowledge

CMD ["python", "-m", "socrat.bot"]
