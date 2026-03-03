#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

cmake -B build -S . -DBUILD_TESTING=ON -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build build
ctest --test-dir build --output-on-failure "$@"
