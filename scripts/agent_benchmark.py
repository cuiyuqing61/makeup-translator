"""Three real product requests + identical warm repeats. Explicitly not a precision evaluation."""
import json
import time
from pathlib import Path
from dotenv import load_dotenv
from makeup_translator.product_agent import run_product_agent
from makeup_translator.product_models import ProductRecord


def main():
    load_dotenv(Path('.env'))
    catalog = json.loads(Path('data/product_catalog.json').read_text())
    flower = next(c for c in catalog if c['brand']=='Flower Knows' and c['category']=='blush')
    rows = [('3ce','腮红','nude peach','中国大陆'),('kate','眼影','RD1','日本'),('花知晓',flower['name'],flower['shade'],None)]
    output = []
    for brand,name,shade,market in rows:
        record = ProductRecord(id='benchmark',raw_brand=brand,raw_name=name,raw_model_or_shade=shade,raw_market=market)
        for mode in ['first','repeat']:
            started = time.monotonic()
            try:
                result = run_product_agent(record,[],lambda *a: None)
                trace = result.get('agent_trace',[])
                output.append({'brand':brand,'mode':mode,'seconds':round(time.monotonic()-started,3),
                    'status':'waiting_user' if result.get('_waiting_user') else result['match_status'],
                    'name':result.get('canonical_name'),'shade':result.get('model_or_shade'),
                    'model_calls':sum(t['name']=='orchestrator' or t.get('internal_model_call',False) for t in trace),
                    'trace':trace})
            except Exception as exc:
                output.append({'brand':brand,'mode':mode,'error':str(exc),'seconds':round(time.monotonic()-started,3)})
            print(json.dumps({k:v for k,v in output[-1].items() if k!='trace'},ensure_ascii=False),flush=True)
    Path('outputs/agent-benchmark.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    main()
