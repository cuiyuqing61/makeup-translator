from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from .product_models import (
    ProductDatabase,
    ProductEnrichment,
    ProductRecord,
    ProductStatus,
    utc_now,
)


DEFAULT_STORE_PATH = Path("runtime/products.json")


class ProductStore:
    def __init__(self, path: Path = DEFAULT_STORE_PATH):
        from .storage import Storage
        self.path = path
        self.db = Storage(path.with_suffix('.sqlite3'))
        if not self.db.get('meta', 'migrated'):
            if path.exists() and path.suffix == '.json':
                self.save(ProductDatabase.model_validate_json(path.read_text(encoding='utf-8')))
            self.db.put('meta', 'migrated', {'done': True})

    def load(self) -> ProductDatabase:
        return ProductDatabase(products=self.db.list('product'))

    def save(self, database: ProductDatabase) -> None:
        with self.db.connect() as db:
            db.execute("DELETE FROM documents WHERE kind='product'")
            for record in database.products:
                db.execute('INSERT INTO documents VALUES (?,?,?)', ('product',record.id,record.model_dump_json()))

    def delete(self, product_id: str):
        self.get(product_id)
        self.db.delete('product', product_id)

    def reset(self, product_id: str, fields: dict | None = None):
        payload = self.get(product_id).model_dump()
        payload.update(fields or {})
        payload.update(status=ProductStatus.draft, enrichment=None, confirmed_at=None, updated_at=utc_now())
        return self._replace(ProductRecord.model_validate(payload))

    def add_draft(
        self,
        brand: str,
        name: str,
        model_or_shade: str | None = None,
        market: str | None = None,
    ) -> ProductRecord:
        record = ProductRecord(
            id=f"product-{uuid4().hex[:12]}",
            raw_brand=brand.strip(),
            raw_name=name.strip(),
            raw_model_or_shade=model_or_shade.strip() if model_or_shade else None,
            raw_market=market.strip() if market else None,
        )
        self.db.put('product', record.id, record.model_dump(mode='json'))
        return record

    def get(self, product_id: str) -> ProductRecord:
        database = self.load()
        record = next((item for item in database.products if item.id == product_id), None)
        if record is None:
            raise KeyError(f"product not found: {product_id}")
        return record

    def _replace(self, updated: ProductRecord) -> ProductRecord:
        with self.db.connect() as db:
            cursor = db.execute("UPDATE documents SET payload=? WHERE kind='product' AND id=?", (updated.model_dump_json(), updated.id))
            if cursor.rowcount != 1:
                raise KeyError(f'product not found: {updated.id}')
        return updated

    def set_pending(
        self, product_id: str, enrichment: ProductEnrichment
    ) -> ProductRecord:
        current = self.get(product_id)
        if current.status != ProductStatus.draft:
            raise ValueError(
                f"only draft can be enriched; current status is {current.status.value}"
            )
        payload = current.model_dump()
        payload.update(
            status=ProductStatus.pending_user_review,
            enrichment=enrichment,
            updated_at=utc_now(),
        )
        return self._replace(ProductRecord.model_validate(payload))

    def confirm(
        self, product_id: str, acknowledge_unverified: bool = False
    ) -> ProductRecord:
        current = self.get(product_id)
        if current.status != ProductStatus.pending_user_review:
            raise ValueError(
                "only pending_user_review can be confirmed; "
                f"current status is {current.status.value}"
            )
        assert current.enrichment is not None
        blocking_issues = current.enrichment.blocking_source_issues()
        if blocking_issues and not acknowledge_unverified:
            raise ValueError(
                "confirmation blocked: "
                + "；".join(blocking_issues)
                + "。确认身份无误时可显式使用 --accept-unverified-sources。"
            )
        now = utc_now()
        payload = current.model_dump()
        payload.update(status=ProductStatus.confirmed, updated_at=now, confirmed_at=now)
        return self._replace(ProductRecord.model_validate(payload))

    def export_confirmed(self) -> list[dict]:
        exported = []
        for product in self.load().products:
            if product.status != ProductStatus.confirmed:
                continue
            item = product.to_owned_product().model_dump(mode='json')
            if not item['color_samples']:
                item.pop('color_samples')
            exported.append(item)
        return exported
