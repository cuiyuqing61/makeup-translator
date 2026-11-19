"""Import observed 3CE shade-selector and KATE public product text; no inferred shades."""
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from makeup_translator.product_agent import TextParser, normalize


def main():
    entries = [e for e in json.loads(Path('data/product_catalog.json').read_text()) if e['brand'] not in {'3CE','KATE'}]
    now = datetime.now(timezone.utc).isoformat()
    raw = Path('/tmp/3ce-catalog.html').read_text()
    variants = json.loads(html.unescape(re.search(r':variants="([^"]+)"', raw)[1]))
    for item in variants:
        entries.append({'id':'3ce-'+item['variantId'],'brand':'3CE','name':item['productName'], 'shade':item['name'],
            'aliases':['腮红','Face Blush','单色腮红'],'category':'blush','market':None,'version':None,
            'source_url':'https://www.3cecosmetics.com/all-products/cheeks/blush/3ce-face-blush',
            'verified_at':now,'verification_status':'official_listing_observed',
            'feed_sha256':hashlib.sha256(raw.encode()).hexdigest(),'evidence':{'name':item['productName'],'shade':item['name']}})
    raw = Path('/tmp/kate-catalog.html').read_text()
    parser = TextParser()
    parser.feed(raw)
    text = ' '.join(parser.parts)
    title = 'ケイト　エレクトリックショックアイズ'
    assert title in text
    for shade in ['ＯＲ－２','ＯＲ－１','ＰＫ－１','ＮＶ－１','ＢＲ－２','ＢＲ－１','ＲＤー１','ＢＲー３']:
        assert shade in text
        entries.append({'id':'kate-electric-'+normalize(shade),'brand':'KATE','name':title, 'shade':shade,
            'aliases':['眼影','电光眼影','Electric Shock Eyes','エレクトリックショックアイズ'],
            'category':'eyeshadow','market':'日本','version':None,
            'source_url':'https://www.kao-kirei.com/ja/item/kbb/kate/4973167517048/',
            'verified_at':now,'verification_status':'official_listing_observed',
            'feed_sha256':hashlib.sha256(raw.encode()).hexdigest(),'evidence':{'name':title,'shade':shade}})
    # Deduplicate repeated variants in the source while preserving the real IDs.
    entries = list({entry['id']:entry for entry in entries}.values())
    Path('data/product_catalog.json').write_text(json.dumps(entries,ensure_ascii=False,indent=2)+'\n')
    print('Source-backed catalog records:', len(entries))


if __name__=='__main__':
    main()
