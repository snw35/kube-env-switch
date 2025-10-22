FROM python:3.13.7-alpine3.22

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir kopf kubernetes

COPY src/envswitch.py .

# Default values — override via Deployment env
ENV WATCH_LABEL_SELECTOR="envswitch=true"
ENV ENV_PATCH_JSON="{}"
ENV MIN_RESTARTS="1"

CMD ["kopf", "run", "--standalone", "--all-namespaces", "/app/envswitch.py"]
