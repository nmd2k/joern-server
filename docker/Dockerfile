# Joern CLI (joern, joern-parse, joern-scan) with HTTP server mode.
# Pin with: docker build --build-arg JOERN_VERSION=v4.0.0 -f docker/Dockerfile .
ARG JOERN_VERSION=

FROM eclipse-temurin:21-jre-jammy

ARG JOERN_VERSION
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
  && apt-get install -y --no-install-recommends curl unzip ca-certificates python3 python3-pip \
  && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --upgrade pip

RUN set -eux; \
  if [ -z "$JOERN_VERSION" ]; then \
    url="https://github.com/joernio/joern/releases/latest/download/joern-cli.zip"; \
  else \
    url="https://github.com/joernio/joern/releases/download/${JOERN_VERSION}/joern-cli.zip"; \
  fi; \
  curl -fsSL -o /tmp/joern-cli.zip "$url"; \
  unzip -q -d /opt/joern /tmp/joern-cli.zip; \
  rm /tmp/joern-cli.zip; \
  chmod +x /opt/joern/joern-cli/joern /opt/joern/joern-cli/joern-parse /opt/joern/joern-cli/joern-scan 2>/dev/null || true

ENV PATH="/opt/joern/joern-cli:${PATH}"

RUN groupadd -r joern -g 1000 \
  && useradd -r -u 1000 -g joern -m -d /home/joern joern \
  && mkdir -p /workspace/ingest /workspace/cpg-out /workspace/cpg-archive \
  && chown -R joern:joern /opt/joern /workspace /home/joern

COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/joern-server-entrypoint.sh
COPY --chmod=755 docker/healthcheck.sh /usr/local/bin/joern-healthcheck.sh
COPY --chmod=755 docker/unified-entrypoint.sh /usr/local/bin/neuralatlas-unified-entrypoint.sh

COPY --chown=joern:joern mcp-joern /app/mcp-joern
COPY --chown=joern:joern joern_server /app/joern_server

# Include committed sample CPG used by integration tests and smoke checks.
# The real Joern server runs inside the container, so the file must be present
# at the in-container path referenced by tests (default: /app/samples/c/hello.cpg).
RUN mkdir -p /app/samples/c
COPY --chown=joern:joern samples/c/hello.cpg /app/samples/c/hello.cpg

RUN python3 -m pip install --no-cache-dir -r /app/mcp-joern/requirements.txt \
  && python3 -m pip install --no-cache-dir "httpx>=0.27.0"

USER joern
WORKDIR /app

EXPOSE 8080
EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=15s --start-period=120s --retries=5 \
  CMD /usr/local/bin/joern-healthcheck.sh

ENTRYPOINT ["/usr/local/bin/neuralatlas-unified-entrypoint.sh"]