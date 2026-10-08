#!/bin/sh
# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

set -eu

mkdir -p /data /data/backups /var/log
chmod +x /app/web/cron/cron-tick.sh

if [ -n "${CUSTOM_DNS:-}" ]; then
  IFS=,
  for ns in $CUSTOM_DNS; do
    ns="$(echo "$ns" | tr -d ' ')"
    [ -n "$ns" ] && echo "nameserver $ns" >> /etc/resolv.conf
  done
  unset IFS
fi

SECRETS_FILE=/data/app-secrets.env

is_unset_secret() {
  case "${1:-}" in
    "" | change-me-in-production | change-cron-secret | change-ingest-secret) return 0 ;;
    *) return 1 ;;
  esac
}

rand_hex() {
  python3 -c "import secrets; print(secrets.token_hex(${1:-32}))"
}

if [ -f "$SECRETS_FILE" ]; then
  # shellcheck disable=SC1090
  . "$SECRETS_FILE"
fi

if is_unset_secret "${SECRET_KEY:-}"; then
  SECRET_KEY="$(rand_hex 32)"
fi
if is_unset_secret "${CRON_SECRET:-}"; then
  CRON_SECRET="$(rand_hex 24)"
fi
if is_unset_secret "${INGEST_SECRET:-}"; then
  INGEST_SECRET="$(rand_hex 24)"
fi

umask 077
printf 'SECRET_KEY=%s\nCRON_SECRET=%s\nINGEST_SECRET=%s\n' "$SECRET_KEY" "$CRON_SECRET" "$INGEST_SECRET" > "$SECRETS_FILE"
chmod 600 "$SECRETS_FILE"
export SECRET_KEY CRON_SECRET INGEST_SECRET

printf 'export CRON_SECRET=%s\nexport PORT=%s\n' "$CRON_SECRET" "${PORT:-8080}" > /app/web/cron/cron-env
chmod 600 /app/web/cron/cron-env

if [ "${USE_SYSTEM_CRON:-false}" = "true" ]; then
  cat > /etc/cron.d/npmtbx <<'CRON'
* * * * * root . /app/web/cron/cron-env; /app/web/cron/cron-tick.sh >> /var/log/npmtbx-cron.log 2>&1

CRON
  chmod 0644 /etc/cron.d/npmtbx
  cron || true
fi

cd /app/web
exec uvicorn app.main:app \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8080}" \
  --proxy-headers \
  --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
