#!/bin/bash
# Builds a disposable repository for the dotfiles prune eval: one merged branch
# whose remote branch is gone and that has a clean linked worktree, plus one
# never-pushed branch. The remote is a local bare repository, so no network is used.
set -euo pipefail

target=${1:?Usage: setup.sh <empty-directory>}
mkdir -p "$target"
cd "$target"

git_() {
  git -c user.name=Eval -c user.email=eval@example.invalid \
    -c commit.gpgsign=false -c core.hooksPath=/dev/null "$@"
}

git_ init -q --bare -b main remote.git
git_ clone -q remote.git demo 2>/dev/null
cd demo
git_ switch -q -c main
echo "demo" > README.md
git_ add README.md
git_ commit -q -m "Initial commit"
git_ push -q -u origin main

git_ switch -q -c feature/done
echo "done" > done.txt
git_ add done.txt
git_ commit -q -m "Add finished feature"
git_ push -q -u origin feature/done
git_ switch -q main
git_ merge -q --ff-only feature/done
git_ push -q origin main
git_ worktree add -q ../demo-done feature/done
git_ --git-dir=../remote.git branch -q -D feature/done

git_ switch -q -c wip
echo "wip" > wip.txt
git_ add wip.txt
git_ commit -q -m "Work in progress"
git_ switch -q main
