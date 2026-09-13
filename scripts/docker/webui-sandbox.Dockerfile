# ============================================================================
# Reference image for sandboxed interactive WebUI pods (Issue #3378)
# ============================================================================
#
# WHAT THIS IS: a working reference build of the image an OpenSandbox
# `endpoints.<tier>.webui_image` entry points at. The control plane creates one
# pod per user instance and runs it as the create request's entrypoint:
#
#   /bin/sh -c "<bootstrap>; <restore gate 300x0.2s>; exec qwen-code-webui \
#     --host 0.0.0.0 --port 3100 --token-secret <per-instance secret> \
#     --quota-check-enabled --auth-type openai"
#
# So the image only has to provide, on PATH for uid 1000:
#   * node + npm-installed qwen-code-webui (and the @qwen-code/qwen-code CLI
#     the webui spawns),
#   * git      - the bootstrap runs `git config --global --add safe.directory
#     /workspace`, and the webui's project features shell out to git,
#   * python3  - the provider's cluster-egress boot probe runs `python3 -c ...`
#     inside the pod; a missing python3 fails the first pod with
#     `egress_probe_unavailable`,
#   * tar      - the session-history snapshot export/import (`tar -cf` /
#     `tar -xf` over execd).
#
# The non-root user is uid/gid 1000 with home /home/agent, matching the sandbox
# pod template's `runAsUser/runAsGroup: 1000`
# (k8s/extras/opensandbox/configmap-sandbox-template.yaml; upstream's
# bootstrap.sh is PID 1 of this container and starts execd under the same
# identity) and the endpoint defaults `exec_uid`/`exec_gid` = 1000. The webui
# session tree lives at /home/agent/.qwen/projects/-workspace - that is the
# directory the control plane snapshots.
#
# THIS IS A REFERENCE BUILD, NOT A PRODUCTION ARTIFACT. The control plane only
# accepts a webui_image that is digest-pinned (`name@sha256:<64 hex>`) AND
# listed in `image_allowlist` - build this, push it to YOUR registry, pin the
# digest, and add it to the allowlist before pointing a tier at it. A tag-only
# reference is refused (`webui_image_not_pinned`); an unlisted one is refused
# (`webui_image_not_allowed`). See docs/sandbox-backends.md section 8 and
# docs/workspace-isolation-capabilities.md section 6.
#
# Build from the repository root (the webui patch scripts are COPYed in):
#   docker build -f scripts/docker/webui-sandbox.Dockerfile \
#     -t <your-registry>/open-ace-webui:<tag> .

ARG BASE_REGISTRY=docker.io
FROM ${BASE_REGISTRY}/node:20-bookworm-slim

# Runtime tools the pod machinery needs (see header). ca-certificates for the
# HTTPS call back to the control-plane LLM proxy.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        python3 \
        tar \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# qwen-code-webui + the qwen-code CLI it drives, pinned to the same pair the
# control-plane image installs (the patch scripts below fail the build on
# version drift). `--prefix /usr` puts the packages at
# /usr/lib/node_modules and the bin at /usr/bin/qwen-code-webui - the layout
# the patch scripts below are hard-coded against.
RUN npm config set registry https://registry.npmmirror.com/ \
    && npm install -g --prefix /usr qwen-code-webui@0.2.40 @qwen-code/qwen-code@0.15.10 \
    && test -x /usr/bin/qwen-code-webui \
    && test -f /usr/lib/node_modules/@qwen-code/qwen-code/cli.js

# The same version-pinned webui patches the control-plane image applies
# (permission handling, conversation-history listing, nav params, vscode
# folder) - without the histories patch the in-app history view stays empty
# even though the control-plane snapshot/restore keeps working.
COPY scripts/patch-qwen-webui-permission.py \
     scripts/patch-qwen-webui-histories.py \
     scripts/patch-qwen-webui-navparams.py \
     scripts/patch-qwen-webui-vscode-folder.py \
     scripts/patch-qwen-webui-local-permission.py \
     /tmp/
RUN python3 /tmp/patch-qwen-webui-permission.py \
    && python3 /tmp/patch-qwen-webui-histories.py \
    && python3 /tmp/patch-qwen-webui-navparams.py \
    && python3 /tmp/patch-qwen-webui-vscode-folder.py \
    && python3 /tmp/patch-qwen-webui-local-permission.py \
    && rm -f /tmp/patch-qwen-webui-*.py

# Non-root runtime user: uid/gid 1000, home /home/agent (the bootstrap mkdirs
# /home/agent and /workspace for this uid; the kubelet mounts the ephemeral
# volumes, the image only needs the account and the HOME to exist).
RUN groupadd -g 1000 agent \
    && useradd -u 1000 -g agent -d /home/agent -s /bin/bash agent \
    && mkdir -p /home/agent /workspace \
    && chown -R agent:agent /home/agent /workspace

USER 1000
WORKDIR /workspace

# In-pod webui port. The control plane's entrypoint passes --host 0.0.0.0
# --port 3100; the browser never reaches this port directly - it goes through
# the control plane's local per-instance port proxy.
EXPOSE 3100
