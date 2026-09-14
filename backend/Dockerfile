# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# libpq is not needed (psycopg binary wheels bundle it); curl is for healthchecks.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

# The neural understanding model is fetched once, at build time, so the running
# container needs no network access to understand a question.
ENV NEURAL_MODEL_CACHE=/app/.model_cache
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/.model_cache')"

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
# Company knowledge pack: policies, rules, knowledge graph, benchmark.
COPY data ./data
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh \
 && mkdir -p /app/data/uploads

# The application runs as an unprivileged user; it never needs to write to /app.
RUN useradd --create-home --uid 10001 praxis \
 && chown -R praxis:praxis /app/data /app/.model_cache
USER praxis

EXPOSE 8000
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
