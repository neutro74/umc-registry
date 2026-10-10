#!/usr/bin/env bash
# Run from a terminal with GitHub access; never force-pushes an existing repository.
set -euo pipefail
registry_root=$(cd "$(dirname "$0")" && pwd)
registry_repo=neutro74/umc-registry
if gh repo view "$registry_repo" --json name >/dev/null 2>&1; then
    if ! git -C "$registry_root" remote get-url origin >/dev/null 2>&1; then
        git -C "$registry_root" remote add origin "https://github.com/$registry_repo.git"
    fi
    git -C "$registry_root" push -u origin main
else
    gh repo create "$registry_repo" --public --source "$registry_root" --remote origin --push \
        --description 'Catalog for sandboxed UMC plugins'
fi
if gh api "repos/$registry_repo/pages" >/dev/null 2>&1; then
    gh api --method PUT "repos/$registry_repo/pages" -f build_type=workflow >/dev/null
else
    gh api --method POST "repos/$registry_repo/pages" -f build_type=workflow >/dev/null
fi
gh workflow run publish.yml --repo "$registry_repo" --ref main
printf 'Registry pushed. Publishing: https://github.com/%s/actions\n' "$registry_repo"
