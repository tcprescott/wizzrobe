# Aerinon's ALttP Door Randomizer behind a small private HTTP API.
# Bump DOOR_RANDO_COMMIT to upgrade; each seed records the version that rolled it.
FROM python:3.12-slim

ARG DOOR_RANDO_REPO=https://github.com/aerinon/ALttPDoorRandomizer.git
ARG DOOR_RANDO_COMMIT=6f390947159b6d2162b52a60aebfd969ccf9b922

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && git init /opt/door-rando \
    && git -C /opt/door-rando fetch --depth 1 "$DOOR_RANDO_REPO" "$DOOR_RANDO_COMMIT" \
    && git -C /opt/door-rando checkout FETCH_HEAD \
    && rm -rf /opt/door-rando/.git \
    && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /srv/requirements.txt
RUN python -m pip install --no-cache-dir \
    -r /opt/door-rando/resources/app/meta/manifests/pip_requirements.txt \
    -r /srv/requirements.txt

COPY server.py /srv/server.py

ENV DOOR_RANDO_DIR=/opt/door-rando \
    DOOR_RANDO_COMMIT=${DOOR_RANDO_COMMIT} \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10002 doorrando
USER doorrando

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health').read()" || exit 1

CMD ["python", "/srv/server.py"]
