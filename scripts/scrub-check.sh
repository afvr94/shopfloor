#!/bin/sh
# Usage: scrub-check.sh <denylist>. Exits 1 if any entry matches tracked files or commit messages.
set -u
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 <denylist>" >&2; exit 2; }
# blank or whitespace-only lines would match everything
pats=$(mktemp) || exit 2
trap 'rm -f "$pats"' EXIT
grep -v '^[[:space:]]*$' "$1" > "$pats"
[ -s "$pats" ] || exit 0
hits=0
git grep -n -i -F -f "$pats" && hits=1
# GitHub adds Co-authored-by trailers on squash merges, and a protected main can't be rewritten to drop them
git log --format=%B | grep -v -i '^co-authored-by:' | grep -n -i -F -f "$pats" && hits=1
exit $hits
