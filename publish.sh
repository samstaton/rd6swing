#!/bin/sh
# Publish the current commit of this (private) repository as one new commit on the public repository.
#   ./publish.sh "rd6swing v4"
# The public history gets one commit per release, each holding the full tree of the published commit.
set -e
cd "$(dirname "$0")"
[ -n "$1" ] || { echo 'usage: ./publish.sh "message"'; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo 'commit or stash your changes first'; exit 1; }
git remote get-url public >/dev/null 2>&1 || git remote add public https://github.com/samstaton/rd6swing.git
if git fetch -q public main 2>/dev/null; then
  # Refuse if the public repository has changes made there (e.g. edited on github.com): publishing
  # this tree on top would silently undo them.
  if ! git log --format=%T | grep -qx "$(git rev-parse 'public/main^{tree}')"; then
    echo 'the public repository has changes that are not in this one:'
    git diff --stat HEAD public/main
    echo 'bring them in first (e.g. git checkout public/main -- README.md), commit, then publish'
    exit 1
  fi
  release=$(git commit-tree "HEAD^{tree}" -p public/main -m "$1")
else
  release=$(git commit-tree "HEAD^{tree}" -m "$1")
fi
git push -q public "$release:refs/heads/main"
echo "published $release to https://github.com/samstaton/rd6swing"
