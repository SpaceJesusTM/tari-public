# Deliberate compatibility pins; update and verify together.
FROM ghcr.io/openclaw/openclaw:2026.9.9
ARG TARGETARCH
ARG GOG_VERSION=0.40.0
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl python3 poppler-utils \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) digest=5f73815950f30de4165b7b767103ca45c4950e84a1da601eda5294e9ff94f767 ;; \
      arm64) digest=21ca9757f67a573115b517854184561cef6b3b73c21e0f60c72229522c7198ac ;; \
      *) echo 'Supported architectures: amd64, arm64' >&2; exit 1 ;; \
    esac; \
    archive="gogcli_${GOG_VERSION}_linux_${TARGETARCH}.tar.gz"; \
    curl -fsSL "https://github.com/openclaw/gogcli/releases/download/v${GOG_VERSION}/${archive}" -o /tmp/gog.tar.gz; \
    echo "$digest  /tmp/gog.tar.gz" | sha256sum -c -; \
    mkdir /tmp/gog-extract; \
    tar -xzf /tmp/gog.tar.gz -C /tmp/gog-extract; \
    install -m 0755 /tmp/gog-extract/gog /usr/local/bin/gog; \
    rm -rf /tmp/gog-extract /tmp/gog.tar.gz
USER node
