FROM python:3.14.0-alpine3.22

ENV PYTHONUNBUFFERED=1

ENV ENVSWITCH_VERSION 0.0.1
ENV KOPF_VERSION 1.38.0
ENV KUBERNETES_VERSION 34.1.0

RUN pip install --no-cache-dir \
    kopf==${KOPF_VERSION} \
    kubernetes==${KUBERNETES_VERSION} \
  && kopf --help

WORKDIR /app
COPY src/envswitch.py .

# Default config values — override via Deployment env settings
ENV WATCH_LABEL_SELECTOR="envswitch=true"
ENV ENV_PATCH_JSON="{}"
ENV MIN_RESTARTS="1"

CMD ["kopf", "run", "--standalone", "--all-namespaces", "/app/envswitch.py"]
