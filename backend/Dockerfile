# syntax=docker/dockerfile:1.7
FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 novacart \
    && useradd --system --uid 10001 --gid novacart --create-home novacart

WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade "pip==25.2" && pip install -r requirements.txt

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN pip install --no-deps .

USER novacart
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
