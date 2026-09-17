#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探测网关是否支持 Responses API 的 image_generation 工具。
只做一件事：发一次请求，打印返回结构，把任何 base64 图像落盘。
不打印 API key。"""
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

WORK = Path("/home/zhong/mydisk/tools/chat/workplace")
IMG_A = WORK / "cb491bad3af4aa8ddb85c90dfebd60b3.jpg"          # 1080x1920 竖图
IMG_B = WORK / "微信图片_20260917185257_21_4.jpg"              # 1706x1279 横图
MODEL = os.environ.get("PROBE_MODEL", "gpt-5.5")


def get_key() -> str:
    """从 ~/.bashrc 取 GPT_API_KEY（非交互 shell 读不到，故手动解析）。"""
    if os.environ.get("GPT_API_KEY"):
        return os.environ["GPT_API_KEY"]
    line = subprocess.run(
        ["grep", "-m1", "^export GPT_API_KEY=", os.path.expanduser("~/.bashrc")],
        capture_output=True, text=True, check=True,
    ).stdout
    val = re.sub(r'^export GPT_API_KEY="?', "", line.strip())
    val = re.sub(r'"?\s*#.*$', "", val).rstrip('"')
    return val


def data_url(p: Path) -> str:
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f"data:image/jpeg;base64,{b64}"


PROMPT = (
    "图1里有两个并排站的人。图2里有另一个人。\n"
    "请以图1为基础合成一张新图：把图2中的那个人放到画面最左侧，"
    "必要时向左扩展画面以留出空间，背景用白色；"
    "插入者的身高与图1中穿黑衣服的那位大致相同。\n"
    "尽量保留所有人原本的面容、发型、服装等细节，光照与透视自然。"
)


def main() -> int:
    key = get_key()
    print(f"[key] 长度 {len(key)}，前缀 {key[:5]}***")
    client = OpenAI(api_key=key, base_url="https://passion8.cc/v1", timeout=180.0)

    content = [
        {"type": "input_text", "text": PROMPT},
        {"type": "input_image", "image_url": data_url(IMG_A)},
        {"type": "input_image", "image_url": data_url(IMG_B)},
    ]
    print(f"[req] model={MODEL}, images={IMG_A.name}, {IMG_B.name}")
    resp = client.responses.create(
        model=MODEL,
        input=[{"role": "user", "content": content}],
        tools=[{"type": "image_generation"}],
    )

    raw = resp.model_dump()
    Path("/tmp/probe_resp.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    print(f"[resp] 顶层字段: {sorted(raw.keys())}")
    print(f"[resp] status={raw.get('status')} error={raw.get('error')}")

    out_items = raw.get("output", [])
    print(f"[resp] output 条目数: {len(out_items)}")
    n_img = 0
    for i, it in enumerate(out_items):
        t = it.get("type")
        print(f"  [{i}] type={t}")
        if t == "image_generation_call":
            print(f"        status={it.get('status')} result_len={len(it.get('result') or '')}")
            b64 = it.get("result")
            if b64:
                out_png = WORK / f"probe_out_{n_img}.png"
                out_png.write_bytes(base64.b64decode(b64))
                n_img += 1
                print(f"        -> 已保存 {out_png} ({out_png.stat().st_size} bytes)")
        elif t == "message":
            for c in it.get("content", []):
                if c.get("type") == "output_text":
                    print(f"        text: {c.get('text','')[:300]}")
    if not n_img:
        print("[warn] 没有拿到任何图像结果")
    return 0 if n_img else 1


if __name__ == "__main__":
    sys.exit(main())
