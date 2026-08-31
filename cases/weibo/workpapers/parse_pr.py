#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从微博业绩公告提取关键数字（打印原文片段供人工核对）。"""
import re, html, sys

def text_of(path):
    t = open(path, encoding='utf-8', errors='ignore').read()
    t = re.sub(r'<[^>]+>', ' ', t)
    return html.unescape(re.sub(r'\s+', ' ', t))

KEYS = [
    r'Fiscal Year 2025 Highlights.{0,2500}',
    r'Monthly active users.{0,200}',
    r'Average daily active users.{0,200}',
    r'annual dividend.{0,300}',
]

def grab(t, pat, n=1):
    out = []
    for m in re.finditer(pat, t, re.I):
        out.append(m.group(0)[:400])
        if len(out) >= n:
            break
    return out

path = sys.argv[1]
t = text_of(path)
print(f"########## {path} ##########")
pats = sys.argv[2:] or KEYS
for p in pats:
    print(f"----- /{p}/")
    for g in grab(t, p, 3):
        print("  ", g, "\n")
