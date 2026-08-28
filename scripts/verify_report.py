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

图表校验（防止 ECharts series 手抄漂移）：图表 series 上方必须紧跟注释锚点
    <!-- vchart src=metrics_X.json path=series scale=100 -->
校验脚本会读取该锚点后最近的 data:[...] 数组，与底稿 path 所指数组逐项比对。
- path: 底稿内的取值路径（点号分隔，支持负数索引，如 summary.roic_series）
- scale: 底稿存小数、图表显示百分数时设为 100

证据指针校验（定性论断防幻觉，report-spec 质量红线）：
    报告中的证据指针写法 [E:<文件名>] 或 [E:<文件名>#补充说明]。
    规则一：指针指向的文件名必须能在 data/manifest.json 的 files[].file 中找到
            （支持前缀匹配，如 manifest 登记 "filings/nvda-*.htm" 可匹配
             [E:filings/nvda-20260125.htm]）。查无此文件 → 失败。
    规则二：五维章节（标题含 商业模式/护城河/增长/管理层/财务质量）各自的
            [E:] 指针数量 ≥ 3，不达标 → 失败（该维度论断没挂够证据）。
    manifest 不存在时此项降级为警告（不拦截），但报告附录必须说明。

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

    def load_json(src, context):
        fp = os.path.join(args.data_dir, src)
        if fp not in cache:
            if not os.path.exists(fp):
                failed.append((src, context, "底稿文件不存在", ""))
                return None
            with open(fp, "r", encoding="utf-8") as f:
                cache[fp] = json.load(f)
        return cache[fp]

    for src, path, fmt, text in matches:
        obj = load_json(src, path)
        if obj is None:
            continue
        value = get_by_path(obj, path)
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

    # ---- 图表数据校验（防止 ECharts series 手抄漂移）----
    # 机制：图表 series 上方紧跟一个 HTML 注释锚点，格式：
    #   <!-- vchart src=metrics_X.json path=series scale=100 -->
    #   {name:'净利率%',type:'line',data:[12.3,24.1,...]}
    # 校验规则：取该锚点后 400 字符内第一个 data:[...] 数组，
    # 解析为数字数组，与底稿 path 指向的数组（乘以 scale，底稿小数→报告百分数时用 100）逐项比对。
    chart_pattern = re.compile(
        r"<!--\s*vchart\s+src=([^\s>]+)\s+path=([^\s>]+)(?:\s+scale=([\d.]+))?\s*-->"
        r"(.{0,400}?)data\s*:\s*\[([^\]]*)\]",
        re.S,
    )
    chart_matches = chart_pattern.findall(html)
    chart_checked = 0
    for src, path, scale_s, _ctx, arr_text in chart_matches:
        obj = load_json(src, f"vchart:{path}")
        if obj is None:
            continue
        scale = float(scale_s) if scale_s else 1.0
        expected = get_by_path(obj, path)
        if expected is KeyError:
            failed.append((src, path, "底稿中无此路径（vchart）", ""))
            continue
        if not isinstance(expected, list):
            failed.append((src, path, "底稿该路径不是数组", ""))
            continue
        nums = []
        try:
            for tok in arr_text.split(","):
                tok = tok.strip().strip("'\"")
                nums.append(round(float(tok) / scale, 10))
        except ValueError:
            failed.append((src, path, "图表 data 数组含无法解析的值", arr_text[:50]))
            continue
        if len(nums) != len(expected):
            failed.append((src, path, f"长度不符：图表 {len(nums)} 项 vs 底稿 {len(expected)} 项", arr_text[:50]))
            continue
        ok = True
        for a, b in zip(nums, expected):
            if b is None:
                continue
            tol = max(abs(float(b)) * 0.005, 0.02)
            if abs(a - float(b)) > tol:
                failed.append((src, path, f"图表数值不一致：图表={a} 底稿={b}", arr_text[:60]))
                ok = False
                break
        if ok:
            chart_checked += 1

    # ---- 证据指针校验（定性论断防幻觉）----
    # [E:文件名] 必须能在 manifest 登记中找到；五维章节各自指针数 ≥ 3。
    epointer_checked = 0
    manifest_fp = os.path.join(args.data_dir, "manifest.json")
    epointers = re.findall(r"\[E:([^\]#]+)(?:#[^\]]*)?\]", html)
    if epointers:
        if not os.path.exists(manifest_fp):
            print("警告：报告含 [E:] 证据指针但 data/manifest.json 不存在，"
                  "无法核验指针指向——报告附录必须说明。")
        else:
            with open(manifest_fp, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            registered = [it.get("file", "") for it in manifest.get("files", [])]

            def in_manifest(name):
                name = name.strip()
                for reg in registered:
                    if reg == name:
                        return True
                    if "*" in reg:  # 通配登记，如 filings/nvda-*.htm
                        rx = "^" + re.escape(reg).replace(r"\*", ".*") + "$"
                        if re.match(rx, name):
                            return True
                return False

            for name in set(epointers):
                if in_manifest(name):
                    epointer_checked += 1
                else:
                    failed.append(("manifest.json", f"E:{name}",
                                   "证据指针指向的文件未在 manifest 登记", ""))
        # 五维章节指针密度检查：按标题切分正文，各维 ≥3 条
        plain = re.sub(r"<script.*?</script>", "", html, flags=re.S)
        dims = ["商业模式", "护城河", "增长", "管理层", "财务质量"]
        heads = [(m.start(), d) for d in dims
                 for m in re.finditer(r"<h[12][^>]*>[^<]*" + d, plain)]
        heads.sort()
        for i, (pos, dim) in enumerate(heads):
            end = heads[i + 1][0] if i + 1 < len(heads) else len(plain)
            cnt = len(re.findall(r"\[E:[^\]]+\]", plain[pos:end]))
            if cnt < 3:
                failed.append(("report", f"五维:{dim}",
                               f"证据指针不足：该维度仅 {cnt} 条 [E:]（要求 ≥3）", ""))
    total = len(matches) + len(chart_matches) + len(set(epointers))
    passed_total = passed + chart_checked + epointer_checked

    print(f"校验完成：{passed_total} 通过 / {len(failed)} 失败 / 共 {total} 项"
          f"（正文数字 {len(matches)} 项 + 图表数组 {len(chart_matches)} 组"
          f" + 证据指针 {len(set(epointers))} 个）")
    if failed:
        for src, path, reason, text in failed:
            print(f"  [FAIL] {src}:{path} — {reason}；报告显示={text}")
        sys.exit(1)
    print("全部通过。可在报告附录写入：数字校验通过（{} 项）。".format(passed))
    sys.exit(0)


if __name__ == "__main__":
    main()
