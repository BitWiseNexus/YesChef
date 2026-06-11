# syntax=docker/dockerfile:1

# --------------------------------------------------------------------- #
# Stage 1: builder — compile Python dependencies into an isolated venv  #
# --------------------------------------------------------------------- #
FROM python:3.11-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --------------------------------------------------------------------- #
# Stage 2 (optional): test — runs the full suite, incl. pexpect         #
# integration tests that need a POSIX pty.                              #
#   docker build --target test .                                        #
# --------------------------------------------------------------------- #
FROM builder AS test

COPY requirements-dev.txt .
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements-dev.txt

WORKDIR /app
COPY chef/ ./chef/
COPY main.py pytest.ini ./
COPY tests/ ./tests/
RUN /opt/venv/bin/python -m pytest

# --------------------------------------------------------------------- #
# Stage 3: runtime — slim image with Python venv, Node.js, Claude Code  #
# --------------------------------------------------------------------- #
FROM python:3.11-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NODE_MAJOR=20

# Node.js (NodeSource) + the Claude Code CLI, then strip apt caches.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg git \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
       | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" \
       > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force \
    && apt-get purge -y curl gnupg \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Non-root user: the agent must never run as root, even inside the sandbox.
RUN useradd --create-home --shell /bin/bash chef \
    && mkdir -p /workspace /app \
    && chown -R chef:chef /workspace /app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=chef:chef chef/ ./chef/
COPY --chown=chef:chef main.py ./

USER chef

ENTRYPOINT ["python", "main.py"]
