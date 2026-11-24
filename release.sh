#!/usr/bin/env bash
# 发版前检查并推送。打标签、建 Release 由 GitHub 自动完成（.github/workflows/release.yml）。
#
#   ./release.sh
#
# 先在 plugin.json 里升好 version、在 CHANGELOG.md 最上面写好这一版，并提交。
# 本脚本检查：在 main 上、改动都已提交、版本号没有发布过、CHANGELOG 有这一节、
# 本地不落后于远端、打包与自检通过；全部通过后推送 main。

set -euo pipefail
cd "$(dirname "$0")"

NAME=$(python3 -c "import json;print(json.load(open('.claude-plugin/plugin.json'))['name'])")
VER=$(python3 -c "import json;print(json.load(open('.claude-plugin/plugin.json'))['version'])")
TAG="v${VER}"
REPO=$(git remote get-url origin | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##')

die() { echo "❌ $*" >&2; exit 1; }

[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || die "当前不在 main 分支。"
[ -z "$(git status --porcelain)" ] || die "还有没提交的改动，先 git add -A && git commit。"
git ls-remote --exit-code --tags origin "refs/tags/${TAG}" >/dev/null 2>&1 \
  && die "${TAG} 已经发布过。请在 .claude-plugin/plugin.json 里升版本号，并在 CHANGELOG.md 补一节。"
grep -q "^## ${TAG}\b" CHANGELOG.md || die "CHANGELOG.md 里没有 “## ${TAG}” 这一节，先写好这一版改了什么。"

git fetch -q origin main
BEHIND=$(git rev-list --count HEAD..origin/main)
[ "$BEHIND" = "0" ] || die "远端 main 比本地新 ${BEHIND} 个提交，先 git pull --rebase，看清楚改了什么再发版。"

./build.sh
[ -f "dist/${NAME}-${VER}.plugin" ] || die "没有找到打包结果 dist/${NAME}-${VER}.plugin。"

git push origin main
echo
echo "✅ 已推送。GitHub 会自动打标签 ${TAG} 并创建 Release，通常一两分钟内完成："
echo "   https://github.com/${REPO}/actions"
echo "   https://github.com/${REPO}/releases/tag/${TAG}"
