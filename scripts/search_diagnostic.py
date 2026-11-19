"""One bounded native search request; reports status, never request headers or keys."""
from pathlib import Path
from dotenv import load_dotenv
from makeup_translator.product_enrichment import SearchController
from makeup_translator.product_agent import read_public_page
from makeup_translator.search_provider import create_product_search

if __name__ == '__main__':
    load_dotenv(Path('.env'))
    client = create_product_search()
    client.identity_domains = ['3cecosmetics.com']
    try:
        sources, attempt = client._run_search(SearchController(client.budget), 'identity_search', '3CE FACE BLUSH NUDE PEACH 官网产品页', 45)
        print({'provider':client.__class__.__name__,'model':getattr(client,'surfing_model',client.model),
               'status':attempt.status,'source_count':len(sources),'urls':[s.get('url') for s in sources[:4]],
               'detail':attempt.detail})
        if sources:
            try:
                page = read_public_page(sources[0]['url'], 8)
                print({'page_read':'success','body_chars':len(page['text']),'url':page['url']})
            except Exception as page_exc:
                print({'page_read':'failed','message':str(page_exc)[:300]})
    except Exception as exc:
        print({'error_type':type(exc).__name__,'message':str(exc)[:400]})
        response = getattr(exc,'response',None)
        if response is not None:
            try:
                body = response.json()
                print({'http_status':response.status_code,'code':body.get('code'),'message':body.get('message')})
            except ValueError:
                print({'http_status':response.status_code})
    print({'usage':getattr(client,'last_search_usage',None)})
