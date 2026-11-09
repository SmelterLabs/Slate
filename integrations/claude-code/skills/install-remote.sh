#!/usr/bin/env bash
# Linux/macOS half of install.ps1 (or use it on its own): put the Slate guide into every agent
# app on this machine. install.ps1 sends the skill as a tar (made from the same git ref) and
# runs this by path:
#   bash install-remote.sh <slate-skill.tar> <sha>
# To use it directly, make the tar yourself from a checkout:
#   mkdir -p /tmp/sk && git archive origin/main integrations/claude-code/skills/slate | tar -x -C /tmp/sk
#   tar -cf /tmp/slate-skill.tar -C /tmp/sk/integrations/claude-code/skills slate
#   bash install-remote.sh /tmp/slate-skill.tar "$(git rev-parse --short origin/main)"
# Targets: ~/.hermes/skills/productivity/slate and every ~/.hermes/profiles/<name>/,
# ~/.claude/skills/slate, ~/.codex/skills/slate (if those apps are here).
# Old copies go to ~/.slate-skill-backup-<time>/. The tar is deleted afterwards.
set -euo pipefail
tarball="$1"
sha="${2:-unknown}"
stage="$(mktemp -d)"
trap 'rm -rf "$stage" "$tarball"' EXIT
tar -xf "$tarball" -C "$stage"
test -f "$stage/slate/SKILL.md" || { echo "no SKILL.md in $tarball" >&2; exit 1; }

targets=()
if [ -d "$HOME/.hermes" ]; then
  targets+=("$HOME/.hermes/skills/productivity/slate")
  for p in "$HOME"/.hermes/profiles/*/; do
    [ -d "$p" ] && targets+=("${p%/}/skills/productivity/slate")
  done
fi
[ -d "$HOME/.claude" ] && targets+=("$HOME/.claude/skills/slate")
[ -f "$HOME/.codex/config.toml" ] && targets+=("$HOME/.codex/skills/slate")

backup="$HOME/.slate-skill-backup-$(date +%Y%m%d-%H%M%S)"
i=0
for t in "${targets[@]}"; do
  if [ -e "$t" ]; then
    i=$((i + 1))
    mkdir -p "$backup/$i"
    echo "$t" > "$backup/$i/FROM.txt"
    mv "$t" "$backup/$i/slate"
  fi
  mkdir -p "$(dirname "$t")"
  cp -r "$stage/slate" "$t"
  echo "$(hostname): installed $sha -> $t"
done
[ "$i" -gt 0 ] && echo "$(hostname): old copies saved in $backup"
exit 0
