"""Import the saved public Shopify product feed, retaining source provenance.

Usage: python scripts/import_flower_catalog.py /tmp/flower-catalog.json
The feed's presence verifies listing text, not regional availability or ownership.
"""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    raw = Path(sys.argv[1]).read_bytes()
    products = json.loads(raw)['products']
    entries = []
    for product in products:
        name = product['title']
        category = ('blush' if 'Blush' in name and 'Brush' not in name else
                    'eyeliner' if 'Eyeliner' in name else 'lip' if 'Lip' in name and product['product_type'] == 'LIPS' else None)
        if not category:
            continue
        for variant in product['variants']:
            entries.append({'id': 'fk-' + str(variant['id']), 'brand': 'Flower Knows', 'name': name,
                'shade': variant['title'], 'market': None, 'version': None, 'category': category,
                'aliases': [], 'source_url': 'https://flowerknows.co/products.json?limit=50',
                'product_handle': product['handle'], 'verified_at': datetime.now(timezone.utc).isoformat(),
                'verification_status': 'official_listing_observed', 'feed_sha256': hashlib.sha256(raw).hexdigest(),
                'evidence': {'name': name, 'shade': variant['title']}})
    if len(entries) < 20:
        raise ValueError('来源中可用记录少于20条，不能凑数')
    Path('data/product_catalog.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n')
    print(f'Imported {len(entries)} source-backed shade records')


if __name__ == '__main__':
    main()
