# ── Build stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir ".[proxy]"

# ── Runtime stage ──────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Token Slayer Proxy" \
      org.opencontainers.image.description="LLM cost-reduction middleware: semantic cache, smart routing, failover" \
      org.opencontainers.image.source="https://github.com/yourusername/claude-context-analyzer"

# Non-root user for security
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/tslayer /usr/local/bin/tslayer

# Data directory for cache and tracker persistence
RUN mkdir -p /data && chown appuser:appuser /data
ENV HOME=/data

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

CMD ["tslayer", "serve", "--host", "0.0.0.0", "--port", "8080"]
