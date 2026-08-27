#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_report.py — 报告数字校验脚本（Phase 5 交付前强制运行）

目的：从 HTML 报告中提取关键数字，与 data/ 底稿逐一比对，防止报告数字与底稿漂移。
不再让模型每次现写校验脚本——统一用本脚本，校验逻辑本身可信且可复现。

机制：报告生成时，关键数字必须用带溯源属性的 span 标签包裹：
    <span class="vnum" data-src="metrics.json" data-path="summary.cagr_total.revenue"
          data-fmt="pct1">12.3%</span>
  - data-src : data/ 目录下的底稿文件名（JSON）
  - data-path: 底稿内取值路径（点号分隔，支持数组下标，如 series.9.roic）
  - data-fmt : 显示格式（pct1=百分比1位小数；num0/num1/num2=千分位数字；raw=原样）

校验规则：按 data-path 取底稿值，按 data-fmt 渲染后与 span 文本比对（数值容差 0.5% 相对误差）。
报告中未包裹 vnum 的数字不校验——但 report-spec 要求所有关键结论数字必须包裹。

用法：
    python3 verify_report.py <report.html> --data-dir <公司名>_analysis/data
退出码：0 全部通过；1 存在不一致或无法溯源。
"""
import argparse
import json
import os
import re
import sys


def get_by_path(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return KeyError
        elif isinstance(cur, dict):
            if part not in cur:
                return KeyError
            cur = cur[part]
        else:
            return KeyError
    return cur


def render(value, fmt):
    if value is None:
        return "缺失"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if fmt == "pct1":
        return f"{v:.1%}"
    if fmt == "pct2":
        return f"{v:.2%}"
    if fmt and fmt.startswith("num"):
        digits = int(fmt[3:]) if len(fmt) > 3 else 0
        return f"{v:,.{digits}f}"
    return str(value)


def parse_number(text):
    """从显示文本中提取数值（去千分位、百分号转小数）"""
    t = text.strip().replace(",", "").replace("，", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    if not m:
        return None
    v = float(m.group())
    if "%" in t:
        v /= 100.0
    return v


def main():
    ap = argparse.ArgumentParser(description="报告数字校验")
    ap.add_argument("report", help="HTML 报告路径")
    ap.add_argument("--data-dir", required=True, help="数据底稿目录")
    args = ap.parse_args()

    with open(args.report, "r", encoding="utf-8") as f:
        html = f.read()

    pattern = re.compile(
        r'<span[^>]*class="[^"]*vnum[^"]*"[^>]*data-src="([^"]+)"[^>]*'
        r'data-path="([^"]+)"[^>]*(?:data-fmt="([^"]*)")?[^>]*>(.*?)</span>',
        re.S,
    )
    matches = pattern.findall(html)
    if not matches:
        print("警告：报告中未发现任何 vnum 溯源标签。按 report-spec，关键结论数字必须可溯源。")
        sys.exit(1)

    cache = {}
    passed, failed = 0, []
    for src, path, fmt, text in matches:
        fp = os.path.join(args.data_dir, src)
        if fp not in cache:
            if not os.path.exists(fp):
                failed.append((src, path, "底稿文件不存在", text))
                continue
            with open(fp, "r", encoding="utf-8") as f:
                cache[fp] = json.load(f)
        value = get_by_path(cache[fp], path)
        if value is KeyError:
            failed.append((src, path, "底稿中无此路径", text))
            continue
        shown = parse_number(re.sub(r"<[^>]+>", "", text))
        expect = None
        try:
            expect = float(value)
            if fmt in ("pct1", "pct2"):
                pass  # 底稿存小数，shown 已转小数
        except (TypeError, ValueError):
            pass
        if shown is None or expect is None:
            # 非数值内容：按渲染文本严格比对
            if re.sub(r"<[^>]+>", "", text).strip() == render(value, fmt):
                passed += 1
            else:
                failed.append((src, path, f"文本不一致：底稿={render(value, fmt)}", text))
            continue
        tol = max(abs(expect) * 0.005, 1e-9)
        if abs(shown - expect) <= tol:
            passed += 1
        else:
            failed.append((src, path, f"数值不一致：底稿={expect}", text.strip()))

    print(f"校验完成：{passed} 通过 / {len(failed)} 失败 / 共 {len(matches)} 项")
    if failed:
        for src, path, reason, text in failed:
            print(f"  [FAIL] {src}:{path} — {reason}；报告显示={text}")
        sys.exit(1)
    print("全部通过。可在报告附录写入：数字校验通过（{} 项）。".format(passed))
    sys.exit(0)


if __name__ == "__main__":
    main()
