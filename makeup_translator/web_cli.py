from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the 妆译 web application.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    uvicorn.run(
        "makeup_translator.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        env_file=str(args.env_file) if args.env_file.exists() else None,
    )


if __name__ == "__main__":
    main()
