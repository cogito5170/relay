FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 relay
WORKDIR /app
COPY pyproject.toml README.md ./
COPY relay ./relay
RUN pip install --no-cache-dir .

USER relay
ENTRYPOINT ["relay", "cloud-run"]
