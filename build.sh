#!/usr/bin/env bash
# 把当前目录打包成可安装的 .plugin。
#
#   ./build.sh
#
# 输出 dist/lets-read-<版本号>.plugin，版本号取自 .claude-plugin/plugin.json。
# 改完内容记得先在 plugin.json 里把 version 加一位，否则新包和旧包同名、分不清。

set -euo pipefail
cd "$(dirname "$0")"

NAME=$(python3 -c "import json;print(json.load(open('.claude-plugin/plugin.json'))['name'])")
VER=$(python3 -c "import json;print(json.load(open('.claude-plugin/plugin.json'))['version'])")
OUT="dist/${NAME}-${VER}.plugin"

# 版本号是否已经打过 tag：打过说明这个版本已经发布，内容再变就该升版本号
if git rev-parse "v${VER}" >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain)" ] || [ "$(git rev-parse HEAD)" != "$(git rev-parse "v${VER}^{commit}")" ]; then
    echo "⚠️  v${VER} 这个版本已经打过 tag，但当前内容和它不一致。"
    echo "    请在 .claude-plugin/plugin.json 里把 version 升一位再打包。"
  fi
fi

# 语法自检：脚本能不能跑
python3 -m compileall -q scripts >/dev/null
find scripts -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# 格式样例必须能通过结构校验：规范和校验脚本对不上时，在这里就会暴露
if ! python3 scripts/check_meta.py references/format-example.md >/dev/null; then
  echo "❌ references/format-example.md 没有通过 check_meta.py，规范与脚本不一致："
  python3 scripts/check_meta.py references/format-example.md || true
  exit 1
fi

# 更新记录里应当有这一版
if ! grep -q "^## v${VER}\b" CHANGELOG.md 2>/dev/null; then
  echo "⚠️  CHANGELOG.md 里还没有 v${VER} 这一节，发版前记得补上。"
fi

rm -rf build dist
mkdir -p "build/${NAME}/skills/${NAME}" dist
cp -r .claude-plugin README.md "build/${NAME}/"
cp -r SKILL.md references scripts "build/${NAME}/skills/${NAME}/"
find build -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

(cd "build/${NAME}" && zip -qr "../../${OUT}" . -x '*.DS_Store')
rm -rf build

echo "✅ ${OUT}"
echo "   $(unzip -l "${OUT}" | tail -1 | awk '{print $2}') 个文件"
echo
echo "下一步：把 ${OUT} 拖进 Claude 安装试用；"
echo "       没问题就提交，再运行 ./release.sh 发版。"
