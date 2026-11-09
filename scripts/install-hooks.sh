#!/bin/sh
# Install the repo's git hooks: the private-terms check on staged files and on the commit message.
# Run once after cloning:  sh scripts/install-hooks.sh
# The hooks check against a denylist kept in the Git directory (never in the repo); without one
# they check nothing. On a commit that has no scripts/check_private_terms.py they do nothing.
set -e
hooks=$(git rev-parse --git-path hooks)
mkdir -p "$hooks"
cat > "$hooks/pre-commit" <<'EOF'
#!/bin/sh
branch=$(git symbolic-ref --short -q HEAD) || exit 1
case "$branch" in
  main|master) echo "Create a branch and open a pull request before committing." >&2; exit 1 ;;
esac
[ -f scripts/check_private_terms.py ] || exit 0
python scripts/check_private_terms.py --staged
EOF
cat > "$hooks/commit-msg" <<'EOF'
#!/bin/sh
[ -f scripts/check_private_terms.py ] || exit 0
python scripts/check_private_terms.py --message "$1"
EOF
chmod +x "$hooks/pre-commit" "$hooks/commit-msg"
echo "hooks installed in $hooks"
list="$(git rev-parse --git-common-dir)/private_terms.sha256"
if [ -f "$list" ]; then
  echo "denylist: $list"
else
  echo "no denylist at $list; the hooks check nothing until one exists (--add WORD creates it)"
fi
