from types import SimpleNamespace

from makeup_translator.photo import observe_photos


def test_vision_request_uses_gpt6_reasoning_instead_of_temperature(monkeypatch):
    requests = []

    class Completions:
        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"eye_notes":"可见观察"}')
            )])

    monkeypatch.setattr('makeup_translator.photo.OpenAI', lambda **kwargs: SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    ))
    monkeypatch.setenv('DASHSCOPE_API_KEY', 'test-key')
    monkeypatch.setenv('QWEN_VISION_MODEL', 'gpt-6-luna')
    monkeypatch.setenv('VISION_REASONING_EFFORT', 'none')
    assert observe_photos([(b'jpg', 'image/jpeg')], 'profile')['eye_notes'] == '可见观察'
    assert requests[-1]['reasoning_effort'] == 'none'
    assert 'temperature' not in requests[-1]

    monkeypatch.setenv('QWEN_VISION_MODEL', 'gpt-4.1-mini')
    observe_photos([(b'jpg', 'image/jpeg')], 'profile')
    assert requests[-1]['temperature'] == 0
    assert 'reasoning_effort' not in requests[-1]
