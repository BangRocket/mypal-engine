# Shared base image for MyPalClara Python services
# Build context: repo root
# Usage: docker build -f services/base/Dockerfile -t clara-base:latest .

FROM python:3.12-slim

WORKDIR /app

# System dependencies (Node.js for MCP servers)
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    git \
    postgresql-client \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Poetry
RUN pip install poetry

# Dependencies
COPY pyproject.toml poetry.lock* ./
RUN poetry config virtualenvs.create false \
    && poetry install --only main --no-interaction --no-ansi

# Playwright browsers
RUN playwright install chromium --with-deps

# Application code
COPY mypalclara/ ./mypalclara/
COPY personalities/ ./personalities/
COPY scripts/ ./scripts/
COPY VERSION ./

# Persistent data directories
RUN mkdir -p /data /app/clara_files /app/mcp_servers
ENV DATA_DIR=/data
ENV CLARA_FILES_DIR=/app/clara_files
ENV MCP_SERVERS_DIR=/app/mcp_servers
