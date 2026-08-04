# syntax=docker/dockerfile:1
#
# Two stages so the runtime image carries no build toolchain or test deps.

FROM python:3.12-slim AS builder

WORKDIR /build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1

# Source copied before install so the package that gets installed is the real
# one, not the placeholder. (An earlier build installed the empty touch'd
# __init__.py and the COPY never reached the install prefix.)
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --prefix=/install .

FROM python:3.12-slim AS runtime

# Unbuffered so `docker logs` shows a tick as it happens rather than in
# 8KB bursts, which makes a hung feed impossible to spot in real time.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ALPHA_DATA=/data

# Non-root: the process needs nothing but its own data directory.
RUN useradd --create-home --uid 10001 alpha \
 && mkdir -p /data \
 && chown -R alpha:alpha /data

COPY --from=builder /install /usr/local

USER alpha
WORKDIR /app
VOLUME ["/data"]

# Reports unhealthy if the database has not been touched recently, which
# catches the failure mode a process check misses: alive but not trading.
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import os,sys,time; p=os.path.join(os.environ.get('ALPHA_DATA','/data'),'alpha_heartbeat'); sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 180 else 1)"

ENTRYPOINT ["python", "-m", "vaisravana_alpha"]
