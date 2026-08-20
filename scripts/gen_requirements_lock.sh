#!/usr/bin/env bash
# Regenerate the hash-pinned production lock (requirements.lock) from
# requirements.txt, resolved against the SAME aliyun mirror the Docker image
# installs from.
#
# Resolving against PyPI can pin versions the mirror lags on (e.g. an lxml patch
# newer than the mirror carries), which then fails `pip install --require-hashes`
# at image build time. Always regenerate with this script so the lock stays
# installable from the mirror.
#
# Requires: uv (https://docs.astral.sh/uv/).
set -euo pipefail
cd "$(dirname "$0")/.."

exec uv pip compile \
  --universal \
  --python-version 3.11 \
  --generate-hashes \
  --index-url https://mirrors.aliyun.com/pypi/simple/ \
  requirements.txt -o requirements.lock
