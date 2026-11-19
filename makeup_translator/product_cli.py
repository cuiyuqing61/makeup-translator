from __future__ import annotations

import argparse
import json
from pathlib import Path

from .product_enrichment import (
    DEFAULT_SEARCH_BUDGET_PATH,
    QwenProductEnricher,
    load_search_budget,
)
from .product_store import DEFAULT_STORE_PATH, ProductStore


def _print_model(model) -> None:
    print(model.model_dump_json(indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create, enrich, review, and confirm owned beauty products."
    )
    parser.add_argument("--store", type=Path, default=DEFAULT_STORE_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Create a draft from user input.")
    add_parser.add_argument("--brand", required=True)
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--model-or-shade")
    add_parser.add_argument("--market")

    subparsers.add_parser("list", help="List products and their states.")

    show_parser = subparsers.add_parser("show", help="Show one product and its evidence.")
    show_parser.add_argument("product_id")

    enrich_parser = subparsers.add_parser(
        "enrich", help="Search the web and create a pending review candidate."
    )
    enrich_parser.add_argument("product_id")
    enrich_parser.add_argument(
        "--search-budget",
        type=Path,
        default=DEFAULT_SEARCH_BUDGET_PATH,
        help="JSON file containing hard search-call and timeout limits.",
    )

    confirm_parser = subparsers.add_parser(
        "confirm", help="Confirm a reviewed pending product."
    )
    confirm_parser.add_argument("product_id")
    confirm_parser.add_argument(
        "--accept-unverified-sources",
        action="store_true",
        help="Confirm identity despite unreachable or unverified official sources.",
    )

    export_parser = subparsers.add_parser(
        "export-confirmed", help="Export confirmed products for tutorial generation."
    )
    export_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    store = ProductStore(args.store)
    try:
        if args.command == "add":
            _print_model(
                store.add_draft(
                    brand=args.brand,
                    name=args.name,
                    model_or_shade=args.model_or_shade,
                    market=args.market,
                )
            )
        elif args.command == "list":
            database = store.load()
            summary = [
                {
                    "id": item.id,
                    "status": item.status.value,
                    "input": " ".join(
                        filter(
                            None,
                            [item.raw_brand, item.raw_name, item.raw_model_or_shade],
                        )
                    ),
                }
                for item in database.products
            ]
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        elif args.command == "show":
            _print_model(store.get(args.product_id))
        elif args.command == "enrich":
            record = store.get(args.product_id)
            import time
            from .tasks import TaskManager, product_enrichment_worker
            from .product_models import ProductEnrichment
            if record.status.value != 'draft':
                raise ValueError('请先将产品撤回草稿再补全')
            budget = load_search_budget(args.search_budget)
            def persist(payload):
                if store.get(record.id).updated_at != record.updated_at:
                    raise ValueError('产品已更新，请重试')
                return store.set_pending(record.id, ProductEnrichment.model_validate(payload)).model_dump(mode='json')
            manager = TaskManager(store.db)
            try:
                task = manager.submit(kind='product_enrichment',worker=product_enrichment_worker,
                    args=(record.model_dump(mode='json'), []),target_id=record.id,
                    timeout_seconds=budget.hard_deadline_seconds,on_success=persist)
                while task.status in {'queued','running'}:
                    time.sleep(.2)
                    task = manager.get(task.id)
                _print_model(task)
            finally:
                manager.shutdown()
        elif args.command == "confirm":
            _print_model(
                store.confirm(
                    args.product_id,
                    acknowledge_unverified=args.accept_unverified_sources,
                )
            )
        elif args.command == "export-confirmed":
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(store.export_confirmed(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(args.output)
    except (KeyError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
