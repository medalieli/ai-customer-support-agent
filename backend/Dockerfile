# syntax=docker/dockerfile:1.7
FROM python:3.12.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
RUN python -m venv /opt/venv
WORKDIR /build
COPY requirements.txt pyproject.toml ./
RUN pip install --upgrade "pip==25.2" "setuptools==78.1.1" "wheel==0.46.2" "jaraco.context==6.1.0" \
    && pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.14.0+cpu" \
    && pip install -r requirements.txt
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
COPY knowledge-base ./knowledge-base
COPY evaluation ./evaluation
RUN pip install --no-deps .
RUN pip uninstall -y setuptools wheel jaraco.context

FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get upgrade -y \
    && apt-get install --no-install-recommends -y postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 novacart \
    && useradd --system --uid 10001 --gid novacart --create-home novacart

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /build /app
RUN mkdir -p /models \
    && chown novacart:novacart /models

USER novacart
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
