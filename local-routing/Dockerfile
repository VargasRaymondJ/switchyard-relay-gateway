# Release binaries and native plugin use the same GNU/Linux libc family.
FROM debian:bookworm-slim@sha256:99c0b25c46d0214d5a2091092e7908907dcaecd6a962cde0ac700acee222b6f3 AS base
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

FROM base AS download
ARG TARGETARCH
COPY checksums.sha256 /tmp/checksums.sha256
WORKDIR /tmp/artifacts
RUN set -eu; \
    case "$TARGETARCH" in amd64) cpu=x86_64; plugin_cpu=x86_64 ;; arm64) cpu=aarch64; plugin_cpu=arm64 ;; *) exit 1 ;; esac; \
    relay="nemo-relay-cli-${cpu}-unknown-linux-gnu-0.11.0"; \
    plugin="switchyard-plugin-0.3.2-linux-${plugin_cpu}.tar.gz"; \
    gateway="agentgateway-linux-${TARGETARCH}"; \
    curl -fSL --retry 3 "https://github.com/NVIDIA/NeMo-Relay/releases/download/0.11.0/${relay}" -o "$relay"; \
    curl -fSL --retry 3 "https://github.com/NVIDIA/NeMo-Relay-Plugins/releases/download/switchyard-plugin-0.3.2/${plugin}" -o "$plugin"; \
    curl -fSL --retry 3 "https://github.com/agentgateway/agentgateway/releases/download/v1.7.0/${gateway}" -o "$gateway"; \
    grep -F "  ${relay}" /tmp/checksums.sha256 | sha256sum -c -; \
    grep -F "  ${plugin}" /tmp/checksums.sha256 | sha256sum -c -; \
    grep -F "  ${gateway}" /tmp/checksums.sha256 | sha256sum -c -; \
    mkdir -p /out/bin /out/plugin; \
    cp "$relay" /out/bin/nemo-relay; cp "$gateway" /out/bin/agentgateway; \
    chmod 755 /out/bin/*; tar xzf "$plugin" -C /out/plugin --strip-components=1

FROM base AS relay
COPY --from=download /out/bin/nemo-relay /usr/local/bin/nemo-relay
# Retain manifest, schema, license and third-party attribution files.
COPY --from=download /out/plugin /opt/switchyard
COPY relay-entrypoint.sh /usr/local/bin/relay-entrypoint
RUN chmod 755 /usr/local/bin/relay-entrypoint \
    && mkdir -p /evidence /home/relay \
    && chown -R 65532:65532 /evidence /home/relay
USER 65532:65532
ENV HOME=/home/relay
ENTRYPOINT ["/usr/local/bin/relay-entrypoint"]

FROM base AS gateway
COPY --from=download /out/bin/agentgateway /usr/local/bin/agentgateway
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/agentgateway"]
CMD ["-f", "/etc/agentgateway/config.yaml"]
