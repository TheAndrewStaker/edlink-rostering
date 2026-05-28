#!/usr/bin/env bash
# Render every D2 source file in docs/design/diagrams/d2/ to SVG in exports/.
#
# Why: D2 source is the diagram-as-code deliverable, but reviewers (and GitHub)
# read the rendered SVG. Committing both keeps the source authoritative and the
# viewable artifact in sync.
#
# Usage:  scripts/render-d2.sh           # render all sources once
#         scripts/render-d2.sh --watch   # watch sources, re-render on save

source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"

if ! command -v d2 >/dev/null 2>&1; then
  cat <<'EOF' >&2
ERROR: d2 binary not found on PATH.

Install D2 (one of):
  Windows:    winget install Terrastruct.D2
              scoop install d2
              choco install d2
  macOS:      brew install d2
  Linux/all:  curl -fsSL https://d2lang.com/install.sh | sh -s --

Then re-run: scripts/render-d2.sh
EOF
  exit 1
fi

d2_dir="../docs/design/diagrams/d2"
exports_dir="${d2_dir}/exports"

mkdir -p "${exports_dir}"

shopt -s nullglob
sources=("${d2_dir}"/*.d2)
shopt -u nullglob

if [[ ${#sources[@]} -eq 0 ]]; then
  echo "No .d2 sources found in ${d2_dir}" >&2
  exit 1
fi

mode="render"
if [[ "${1:-}" == "--watch" ]]; then
  mode="watch"
fi

for source in "${sources[@]}"; do
  name="$(basename "${source}" .d2)"
  out="${exports_dir}/${name}.svg"
  if [[ "${mode}" == "watch" ]]; then
    echo "Watching ${name}.d2 → exports/${name}.svg (Ctrl-C to stop)"
    d2 --watch --theme=0 --layout=elk --pad=40 "${source}" "${out}"
  else
    echo "Rendering ${name}.d2 → exports/${name}.svg"
    d2 --theme=0 --layout=elk --pad=40 "${source}" "${out}"
  fi
done

if [[ "${mode}" == "render" ]]; then
  echo
  echo "Done. ${#sources[@]} diagram(s) rendered to ${exports_dir}/"
fi
