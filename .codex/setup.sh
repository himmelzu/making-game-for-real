#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
python3 --version
node --version
python3 -m py_compile tools/*.py
python3 -m py_compile automation/main.py automation/fm26auto/*.py
node --check automation/ui/app.js
for web_script in web/*.js; do
  node --check "$web_script"
done
node --test tests/*.cjs
python3 -m unittest discover -s tests -p 'test_*.py'
