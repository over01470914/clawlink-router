"""ClawLink Router - Entry point for running the server."""

import json
import logging
import os
import socket
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def _check_existing_router(host: str, port: int) -> bool:
    try:
        with urlopen(f"http://{host}:{port}/health", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False

    return payload.get("status") == "healthy"


def _handle_port_in_use(host: str, port: int) -> None:
    if _check_existing_router("127.0.0.1", port):
        answer = input(
            f"ClawLink Router is already running at http://127.0.0.1:{port}. Restart it? [y/N]: "
        ).strip().lower()
        if answer != "y":
            logger.info("Startup cancelled by user.")
            raise SystemExit(0)

        pid = _find_listener_pid(port)
        if pid is None:
            logger.error("Could not resolve the process ID listening on port %s.", port)
            raise SystemExit(1)

        _terminate_process(pid)
        logger.info("Stopped process %s on port %s. Starting a new instance...", pid, port)
        return

    logger.error(
        "Port %s is already in use. Stop the existing process or set CLAWLINK_ROUTER_PORT to another port.",
        port,
    )
    raise SystemExit(1)


def _find_listener_pid(port: int) -> int | None:
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.SubprocessError:
            return None

        token = f":{port}"
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            local_addr = parts[1]
            state = parts[3].upper()
            pid_text = parts[4]
            if local_addr.endswith(token) and state == "LISTENING" and pid_text.isdigit():
                return int(pid_text)
        return None

    try:
        result = subprocess.run(
            ["lsof", "-i", f"TCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    pid_text = result.stdout.strip().splitlines()
    if not pid_text:
        return None
    if pid_text[0].isdigit():
        return int(pid_text[0])
    return None


def _terminate_process(pid: int) -> None:
    current_pid = os.getpid()
    if pid == current_pid:
        logger.error("Refusing to terminate current process (pid=%s).", pid)
        raise SystemExit(1)

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to terminate process %s: %s", pid, exc.stderr.strip())
            raise SystemExit(1) from exc
        return

    try:
        os.kill(pid, 15)
    except OSError as exc:
        logger.error("Failed to terminate process %s: %s", pid, exc)
        raise SystemExit(1) from exc


def main() -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        if exc.name == "uvicorn":
            logging.getLogger(__name__).error(
                "Missing runtime dependency 'uvicorn'. Install router dependencies first, for example: python -m pip install -e ."
            )
            raise SystemExit(1) from exc
        raise

    host = os.getenv("CLAWLINK_ROUTER_HOST", "0.0.0.0")
    port = int(os.getenv("CLAWLINK_ROUTER_PORT", "8420"))

    if _is_port_open("127.0.0.1", port):
        _handle_port_in_use(host, port)

    try:
        uvicorn.run(
            "clawlink_router.api:app",
            host=host,
            port=port,
            log_level="info",
            reload=False,
        )
    except OSError as exc:
        if getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) == 10048:
            _handle_port_in_use(host, port)
            os.execv(sys.executable, [sys.executable, *sys.argv])
        raise


if __name__ == "__main__":
    main()
