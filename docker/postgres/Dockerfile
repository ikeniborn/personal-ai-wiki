# Custom Postgres 16 image: pgvector (from base) + Apache AGE release/PG16/1.5.0.
# Build context is this directory: `docker build -t paw/postgres:pg16-age docker/postgres`.
FROM pgvector/pgvector:pg16

USER root
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential git ca-certificates flex bison postgresql-server-dev-16; \
    git clone --branch release/PG16/1.5.0 --depth 1 https://github.com/apache/age.git /tmp/age; \
    cd /tmp/age && make && make install; \
    cd / && rm -rf /tmp/age; \
    apt-get purge -y --auto-remove build-essential git flex bison postgresql-server-dev-16; \
    rm -rf /var/lib/apt/lists/*
USER postgres
