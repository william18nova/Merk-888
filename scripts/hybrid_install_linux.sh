#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
chmod u+x "$APP_DIR/NovaPOS"
"$APP_DIR/NovaPOS" --install
