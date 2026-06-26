# ──────────────────────────────────────────────────────────────────
# OhMyCaptcha — Dockerfile
# Uses Python 3.11 slim, installs Firefox/Playwright system deps and
# all Python dependencies, then runs the service on port 8000.
# ──────────────────────────────────────────────────────────────────

FROM python:3.11-slim

LABEL org.opencontainers.image.title="OhMyCaptcha"
LABEL org.opencontainers.image.description="Captcha solving service with Playwright Firefox (invisible_playwright), Ollama vision models, and Whisper audio transcription."
LABEL org.opencontainers.image.version="3.0.0"

# ── Prevent Python from writing .pyc / buffering stdout ──
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

# ── 1. System dependencies ──────────────────────────────────
#   ffmpeg       — Whisper audio decoding
#   xvfb         — invisible_playwright headless mode (Xvfb virtual display)
#   libgtk-3-0   — Firefox rendering
#   libdbus-glib-1-2 — Firefox D-Bus integration
#   build-essential + libffi-dev — Python C extensions (whisper, etc.)
#   curl         — health checks
#   git          — pip install from repos if needed
#   Also install all Playwright Firefox system deps via the playwright CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    xvfb \
    curl \
    git \
    build-essential \
    libffi-dev \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Python virtual environment ───────────────────────────
RUN python3 -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# ── 3. Install Python dependencies ──────────────────────────
# Core runtime
RUN pip install --no-cache-dir --root-user-action=ignore \
    fastapi \
    uvicorn[standard] \
    httpx \
    aiohttp \
    pydantic \
    Pillow \
    openai \
    "openai-whisper>=20231117" \
    playwright

# invisible-playwright is NOT on PyPI — install from GitHub
RUN pip install --no-cache-dir --root-user-action=ignore \
    "invisible-playwright @ git+https://github.com/feder-cr/invisible_playwright.git"

# ── 4. Install Playwright Firefox system deps ───────────────
# This downloads the standard Firefox binary that Playwright knows about
# and installs all required system libraries.  invisible_playwright
# downloads its OWN patched Firefox binary at runtime, but still needs
# the system libraries that `playwright install-deps` provides.
RUN playwright install-deps firefox

# ── 5. Copy application source ──────────────────────────────
COPY . .

# ── 6. Create non-root user for security ────────────────────
RUN groupadd -r ohmycaptcha && useradd -r -g ohmycaptcha -d /app -s /sbin/nologin ohmycaptcha \
    && chown -R ohmycaptcha:ohmycaptcha /app
USER ohmycaptcha

# ── 7. Port and command ─────────────────────────────────────
EXPOSE 8000

# The service entrypoint is main.py which runs uvicorn on src.main:app
CMD ["python", "main.py"]
