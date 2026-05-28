#!/usr/bin/env bash
# Production build of the Chakra admin app.

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

cd web
npm run build
