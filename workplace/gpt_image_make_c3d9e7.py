#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Responses API 的 image_generation 工具做图像合成。
正确角色：
  底图 = 微信图片_20260917185257_21_4.jpg（两人合影，浅墙背景，横向 1706x1279）
  插入 = cb491bad3af4aa8ddb85c90dfebd60b3.jpg（单人，昏暗室内背景，竖向 1080x1920）
目标：插入者置于最左侧，左侧扩展为白色背景，身高与底图中黑衣者相近。
"""
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

WORK = Path("/home/zhong/mydisk/tools/chat/workplace")
BASE = WORK / "微信图片_20260917185257_21_4.jpg"      # 两人合影（底图）
INS = WORK / "cb491bad3af4aa8ddb85c90dfebd60b3.jpg"    # 单人（插入者）
OUT = WORK / "合成图_三人_e7a1c3.png"
MODEL = os.environ.get("GEN_MODEL", "gpt-5.5")
SIZE = os.environ.get("GEN_SIZE", "1536x1024")          # 横构图

PROMPT = """图1 是基础合影照片，里面有两个人：
- 画面中间偏左的人穿黑色短袖上衣，个子较高；
- 画面右侧的人穿白色短袖上衣、戴眼镜，比左边那位矮约半个头。
背景是一面浅色的墙，右侧边缘有一条木色竖向边框。

图2 是另一个人的单人照：穿深色外套、里面是浅色（白色）领口的上衣，肩部有浅色条纹。
这张单人照原本是在昏暗的室内拍的，背景又暗又杂。

任务：以【图1】为底图，把【图2】里的这个人合成进画面，生成一张三人合影。

硬性要求：
1. 新人放在画面【最左侧】，原有两个人保持在各自原来的位置；
2. 需要时把画面【向左扩展】以给新人腾出空间，扩展出来的区域以及新人身后
   的背景一律用【纯白色】，必须彻底去掉图2里那个昏暗的室内背景；
3. 新人的【身高与图1中间穿黑色短袖的那位大致相同】；
4. 图1原有两人的面貌、发型、服装、姿态、站位和光照保持【完全不变】；
5. 图2这个人的面貌、发型、服装细节也要尽量保持；
6. 三个人在同一地平线上站立，透视和光照自然，拼接处不要有生硬的接缝或白边；
7. 整张图保持横向构图，白色背景干净均匀。
"""


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


def main() -> int:
    client = OpenAI(api_key=get_key(), base_url="https://passion8.cc/v1", timeout=600.0)
    print(f"[req] model={MODEL} size={SIZE}")
    print(f"[req] 底图={BASE.name}  插入={INS.name}")

    resp = client.responses.create(
        model=MODEL,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": PROMPT},
            {"type": "input_image", "image_url": data_url(BASE)},
            {"type": "input_image", "image_url": data_url(INS)},
        ]}],
        tools=[{"type": "image_generation", "size": SIZE}],
    )

    raw = resp.model_dump()
    Path("/tmp/gen_resp.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    print(f"[resp] status={raw.get('status')} error={raw.get('error')}")

    saved = 0
    for it in raw.get("output", []):
        if it.get("type") == "image_generation_call":
            print(f"[img] status={it.get('status')} size_hint={it.get('size')} "
                  f"revised_prompt={(it.get('revised_prompt') or '')[:200]!r}")
            if it.get("result"):
                OUT.write_bytes(base64.b64decode(it["result"]))
                saved += 1
                print(f"[img] 已保存 {OUT} ({OUT.stat().st_size} bytes)")
        elif it.get("type") == "message":
            for c in it.get("content", []):
                if c.get("type") == "output_text":
                    print(f"[text] {c['text'].strip()[:400]}")
    if not saved:
        print("[warn] 未拿到图像")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
