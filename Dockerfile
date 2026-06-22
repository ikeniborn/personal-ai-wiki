FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv pip install --system .

# api: uvicorn; worker: arq; init: alembic upgrade head  (entrypoint chosen in compose)
EXPOSE 8000
CMD ["uvicorn", "paw.main:app", "--host", "0.0.0.0", "--port", "8000"]
