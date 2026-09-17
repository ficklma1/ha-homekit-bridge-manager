#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

export REFRESH_SECONDS="$(bashio::config 'refresh_seconds')"
export LOG_LEVEL="$(bashio::config 'log_level')"
export ASSUME_NOT_IN_HOMEKIT="$(bashio::config 'assume_not_in_homekit | join(",")')"
export HA_CONFIG_DIR="/homeassistant"
export PORT="8099"

bashio::log.info "Starting HomeKit Bridge Manager (refresh every ${REFRESH_SECONDS}s)"

cd /opt/hkbm
exec python3 -m app.server
