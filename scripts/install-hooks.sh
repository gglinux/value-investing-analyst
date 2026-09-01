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
echo "已安装的闸门："
echo "  pre-commit  — 测试套件全绿 + 暂存区隐私/临时文件扫描"
echo "  commit-msg  — 提交信息乱码/噪声检测（须在此阶段做：pre-commit 时"
echo "                本次提交信息尚未写入，读到的是上一次的陈旧内容）"
echo "紧急绕过：git commit --no-verify"
