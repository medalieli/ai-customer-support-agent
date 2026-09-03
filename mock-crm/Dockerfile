# syntax=docker/dockerfile:1.7
FROM python:3.12.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN groupadd --system --gid 10003 crm && useradd --system --uid 10003 --gid crm --create-home crm
WORKDIR /service
COPY requirements.txt ./
RUN pip install --upgrade "pip==25.2" && pip install -r requirements.txt
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-deps . && mkdir -p /data && chown crm:crm /data
USER crm
EXPOSE 8090
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8090"]
