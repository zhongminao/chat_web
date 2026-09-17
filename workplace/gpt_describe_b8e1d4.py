#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用网关的视觉能力描述图片内容（我看不到图，靠这个建立事实）。
用法: python gpt_describe_b8e1d4.py <图片路径> [<图片路径> ...]"""
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

from openai import OpenAI


def get_key() -> str:
    if os.environ.get("GPT_API_KEY"):
        return os.environ["GPT_API_KEY"]
    line = subprocess.run(
        ["grep", "-m1", "^export GPT_API_KEY=", os.path.expanduser("~/.bashrc")],
        capture_output=True, text=True, check=True).stdout
    val = re.sub(r'^export GPT_API_KEY="?', "", line.strip())
    return re.sub(r'"?\s*#.*$', "", val).rstrip('"')


def data_url(p: Path) -> str:
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(p.read_bytes()).decode()


ASK = ("请客观描述这张图，包含：1) 画面的长宽比方向（横/竖/方）；"
       "2) 有几个人，各自在画面什么水平位置（左/中/右）；"
       "3) 每个人的衣着颜色、大致身高关系（谁高谁矮、差多少）；"
       "4) 背景是什么颜色/场景；5) 是照片还是插画风格。"
       "只描述看到的事实，不要猜测，不要评价。")


def main(paths):
    if not paths:
        print("用法: python gpt_describe_b8e1d4.py <图片> [<图片> ...]")
        return 2
    client = OpenAI(api_key=get_key(), base_url="https://passion8.cc/v1", timeout=180.0)
    for p_str in paths:
        p = Path(p_str)
        from PIL import Image
        im = Image.open(p)
        print(f"\n===== {p.name}  ({im.size[0]}x{im.size[1]}) =====")
        resp = client.responses.create(
            model="gpt-5.5",
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": ASK},
                {"type": "input_image", "image_url": data_url(p)},
            ]}],
        )
        for it in resp.model_dump().get("output", []):
            if it.get("type") == "message":
                for c in it.get("content", []):
                    if c.get("type") == "output_text":
                        print(c["text"].strip())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
