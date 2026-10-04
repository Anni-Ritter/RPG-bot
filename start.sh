#!/bin/sh
set -eu
mkdir -p /app/data
exec python -m app.main
