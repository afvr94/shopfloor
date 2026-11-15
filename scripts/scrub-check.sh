#!/bin/sh
# Usage: scrub-check.sh <denylist>. Exits 1 if any entry matches tracked files or commit messages.
set -u
[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 <denylist>" >&2; exit 2; }
hits=0
git grep -n -i -F -f "$1" && hits=1
git log --format=%B | grep -n -i -F -f "$1" && hits=1
exit $hits
