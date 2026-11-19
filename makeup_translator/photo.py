import base64
import os
from openai import OpenAI
from .providers import _extract_json


def observe_photos(images: list[tuple[bytes, str]], purpose: str):
    key = os.getenv('DASHSCOPE_API_KEY')
    if not key:
        raise ValueError('未配置视觉分析服务；可以手动填写资料。')
    client = OpenAI(api_key=key, base_url=os.getenv('QWEN_BASE_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1'), timeout=45, max_retries=0)
    if purpose == 'product_color':
        prompt = ('只定位打开包装后实际可见的彩妆膏体、粉体或液体，不把外壳、印刷色卡、反光或阴影当作产品颜色。'
                  '返回 JSON：{"regions":[{"label":"盘内色块名称或编号","box":[x1,y1,x2,y2]}]}。'
                  '坐标均为相对原图宽高的 0 到 1 数值；每个色块单独一个框，框应位于色块内部。'
                  '不能可靠定位时返回空数组。不要输出 RGB、色号或品牌。')
    elif purpose == 'product':
        prompt = '读取包装可见文字，返回JSON：brand、name、model_or_shade、market（字符串，无法看清填空字符串）、notes（字符串）。不猜测色号，不根据照片颜色判断细微色差。'
    else:
        from .face_profile import analysis_prompt
        prompt = analysis_prompt()
    content = [{'type':'text', 'text':prompt}]
    for data, mime in images:
        content.append({'type':'image_url', 'image_url':{'url':f'data:{mime};base64,' + base64.b64encode(data).decode()}})
    model = os.getenv('QWEN_VISION_MODEL', 'qwen3-vl-plus')
    request = {'model': model, 'messages': [{'role': 'user', 'content': content}]}
    if model.startswith('gpt-6-') or model.startswith('gpt-6.'):
        request['reasoning_effort'] = os.getenv('VISION_REASONING_EFFORT',
            'none' if model == 'gpt-6-luna' else 'low')
    else:
        request['temperature'] = 0
    result = client.chat.completions.create(**request)
    return _extract_json(result.choices[0].message.content or '{}')
