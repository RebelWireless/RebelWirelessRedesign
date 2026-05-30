# ===== Rebel Wireless — Dockerfile =====
# Multi-stage: build deps in a base, then slim runtime

FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------- Runtime ----------
FROM python:3.12-slim

# Create non-root user for security
RUN groupadd -r rebel && useradd -r -g rebel rebel

WORKDIR /app

# Copy only what's needed
COPY --from=builder /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=builder /usr/local/bin/ /usr/local/bin/

# Application code
COPY app.py .
COPY coverage-data.json .
COPY templates/ templates/

# Mount point for external coverage-data editing
VOLUME /app/coverage-data.json

EXPOSE 5000

USER rebel

# Run with gunicorn — production WSGI server
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "30", "app:app"]
