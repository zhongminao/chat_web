"""启动入口：python -m chat [port]

默认端口 8200。在任意目录下均可启动（chat 已 pip install -e 到当前环境）。
"""
import sys

import uvicorn

from chat.app import app


def main() -> None:
    port = 8200
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        port = int(sys.argv[1].strip())

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
    )


if __name__ == "__main__":
    main()
