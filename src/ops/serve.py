"""进程退出后重新拉起网页服务。"""

from __future__ import annotations


def serve_command() -> list[str]:
    return [
        "uv", "run", "uvicorn", "src.web.app:create_app",
        "--factory", "--host", "0.0.0.0", "--port", "8080",
    ]


def main() -> None:
    from src.ops.phase6_release import supervise_process

    supervise_process(serve_command(), max_restarts=100, ui=lambda _event: None)


if __name__ == "__main__":
    main()
