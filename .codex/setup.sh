#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python3 --version
node --version
python3 -m py_compile tools/*.py
node --test tests/*.cjs
python3 -m unittest discover -s tests -p 'test_*.py'
