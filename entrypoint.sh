#!/bin/sh
set -eu
umask 077
mkdir -p "${LEAKSCOPE_DATA_DIR:-/data/app}" "${LEAKSCOPE_EXPORT_DIR:-/data/artifacts/exports}"
exec "$@"
