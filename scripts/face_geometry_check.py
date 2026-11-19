"""Inspect landmark crops without sending an image to any external service."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from makeup_translator.facial_images import baseline_step


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    boxes = {}
    for part in ("left_eye", "left_cheek", "lip"):
        boxes[part] = baseline_step(args.image, part, args.output / f"{part}.png")["box"]
    print(json.dumps({"seconds": round(time.monotonic()-started, 2), "boxes": boxes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
