#!/bin/sh
set -eu

if [ -z "${USERNAME:-}" ] || [ -z "${PASSWORD:-}" ]; then
  echo "ERROR: USERNAME and PASSWORD environment variables are required." >&2
  exit 1
fi

mkdir -p /root/.dhapi
cat > /root/.dhapi/credentials <<EOF
[default]
username = "${USERNAME}"
password = "${PASSWORD}"
EOF

exec "$@"
