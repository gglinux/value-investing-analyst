#!/bin/bash
# 安装 git 钩子。.git/hooks 不受版本控制，故钩子源码存放在 scripts/hooks/，
# 由本脚本软链到 .git/hooks/ —— 这样钩子本身也能被 review 和迭代。
#
# 用法：bash scripts/install-hooks.sh
set -eu

REPO_ROOT="$(git rev-parse --show-toplevel)"
SRC="$REPO_ROOT/scripts/hooks"
DST="$REPO_ROOT/.git/hooks"

mkdir -p "$DST"
for h in "$SRC"/*; do
  name="$(basename "$h")"
  chmod +x "$h"
  ln -sf "../../scripts/hooks/$name" "$DST/$name"
  echo "✓ 已安装钩子: $name → .git/hooks/$name"
done
echo
echo "验证：git commit 时将自动运行测试套件 + 提交信息噪声检测 + 隐私扫描。"
echo "紧急绕过：git commit --no-verify"
