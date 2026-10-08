#!/bin/sh

# Copyright (C) 2026 https://ludditious.com/
#
#     This program is free software: you can redistribute it and/or modify
#     it under the terms of the GNU Affero General Public License as published by
#     the Free Software Foundation, either version 3 of the License, or
#     (at your option) any later version.
#
#     This program is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public License
#     along with this program.  If not, see <https://www.gnu.org/licenses/>.

set -eu

. /app/web/cron/cron-env

PORT="${PORT:-8080}"

SECRET="${CRON_SECRET:?CRON_SECRET is required}"

TS="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

HTTP="$(curl -sS -o /tmp/agh-cron-out -w "%{http_code}" -X POST "http://127.0.0.1:${PORT}/internal/cron/tick" \

  -H "Authorization: Bearer ${SECRET}" || echo "000")"

echo "${TS} HTTP ${HTTP} $(cat /tmp/agh-cron-out 2>/dev/null || true)"

rm -f /tmp/agh-cron-out


