from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import OwnedProduct, UserProfile
from .pipeline import MakeupTutorialPipeline


def _load_model(path: Path, model_type):
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a makeup tutorial from a reference image.")
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--profile", type=Path, help="User profile JSON. Omitted means generic defaults."
    )
    parser.add_argument(
        "--products", type=Path, help="Confirmed product export. Omitted means no products."
    )
    parser.add_argument("--provider", choices=["fixture", "qwen"], default="fixture")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    profile = _load_model(args.profile, UserProfile) if args.profile else UserProfile()
    raw_products = (
        json.loads(args.products.read_text(encoding="utf-8")) if args.products else []
    )
    products = [OwnedProduct.model_validate(item) for item in raw_products]
    try:
        result = MakeupTutorialPipeline(provider=args.provider).run(args.image, profile, products)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    rendered = result.model_dump_json(indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(args.output)
    else:
        print(rendered)


if __name__ == "__main__":
    main()
