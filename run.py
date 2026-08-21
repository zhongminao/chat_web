#!/usr/bin/env python
import signal
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def is_port_in_use(
    port: int,
    ) -> bool:
    result = subprocess.run(
        [
            "ss",
            "-ltnp",
            f"( sport = :{port} )",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return f":{port}" in result.stdout


def main(
    ) -> None:
    port = 8200
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        port = int(sys.argv[1].strip())

    if is_port_in_use(port):
        result = subprocess.run(
            [
                "ss",
                "-ltnp",
                f"( sport = :{port} )",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.stdout.strip():
            print(result.stdout, end="")
        raise SystemExit(f"port {port} is already in use")

    command = [
        sys.executable,
        "-u",
        "-m",
        "uvicorn",
        "app:app",
        "--app-dir",
        str(BASE_DIR),
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    process = subprocess.Popen(command, cwd=str(BASE_DIR))

    def terminate_process(
        ) -> None:
        if process.poll() is not None:
            return

        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def handle_signal(
        signum: int,
        frame: object,
        ) -> None:
        raise KeyboardInterrupt from None

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, handle_signal)

    try:
        process.wait()
    except KeyboardInterrupt:
        pass
    finally:
        terminate_process()


if __name__ == "__main__":
    main()
