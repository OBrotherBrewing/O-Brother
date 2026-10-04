#!/usr/bin/env bash
# Turns each draft in queue/ into its own pull request so an editor can approve (merge)
# or reject (close) it from the GitHub app. Run by .github/workflows/pipeline.yml.
set -euo pipefail
base="${BASE_BRANCH:-main}"
git config user.name "${GIT_BOT_NAME:-newsroom-bot}"
git config user.email "${GIT_BOT_EMAIL:-newsroom-bot@users.noreply.github.com}"

# 1. Save pipeline state (seen URLs, audit log) on the base branch.
git add data/seen.json data/audit.jsonl 2>/dev/null || true
git commit -m "pipeline: state $(date -u +%Y-%m-%dT%H:%MZ) [skip ci]" || true
git push origin "HEAD:$base"

gh label create story --color F0A202 --description "Draft story awaiting editorial review" --force >/dev/null 2>&1 || true

shopt -s nullglob
for dir in queue/*/; do
  id="$(basename "$dir")"
  branch="review/$id"
  if git ls-remote --exit-code --heads origin "$branch" >/dev/null 2>&1; then
    echo "skip $id: branch exists"; continue
  fi
  title="$(python - "$dir/story.md" <<'PY'
import sys, yaml
text = open(sys.argv[1], encoding="utf-8").read()
print(yaml.safe_load(text.split("---", 2)[1])["title"])
PY
)"
  cp "$dir/REVIEW.md" "/tmp/review-$id.md"
  git checkout -q -b "$branch" "origin/$base" 2>/dev/null || git checkout -q -b "$branch" "$base"
  cp -r "$dir" "/tmp/queue-$id"
  mkdir -p "queue/$id" && cp -r "/tmp/queue-$id/." "queue/$id/"
  GNN_NO_AUDIT=1 python -m gnn stage "$id"
  git add content/stories data/evidence
  git commit -q -m "Draft for review: $title"
  git push -q origin "$branch"
  gh pr create --base "$base" --head "$branch" --title "Review: $title" --body-file "/tmp/review-$id.md" --label story
  git checkout -q "$base"
  rm -rf "queue/$id"
done
