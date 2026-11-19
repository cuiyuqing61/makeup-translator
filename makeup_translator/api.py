from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from .models import StrictModel, UserProfile
from .product_models import ProductEnrichment, ProductRecord
from .product_store import DEFAULT_STORE_PATH, ProductStore
from .tasks import (
    TaskBudget,
    TaskManager,
    TaskSnapshot,
    load_task_budget,
    product_enrichment_worker,
    tutorial_worker,
    tutorial_analysis_worker,
    tutorial_steps_worker,
    tutorial_products_worker,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FRONTEND_DIST = PROJECT_ROOT / "web" / "dist"
MAX_IMAGE_BYTES = 12 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _key_edit_orders(steps: list[dict], limit: int = 4) -> set[int]:
    """Spend edits on steps that actually change makeup, covering distinct regions first."""
    editable = [step for step in steps
                if (step.get('product_match') or {}).get('status') != 'not_needed']
    selected: list[int] = []
    seen: set[str] = set()
    for step in editable:
        if step['region'] not in seen and len(selected) < limit:
            selected.append(step['order'])
            seen.add(step['region'])
    for step in editable:
        if len(selected) < limit and step['order'] not in selected:
            selected.append(step['order'])
    return set(selected)


class ProductCreateRequest(StrictModel):
    brand: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)
    model_or_shade: str | None = Field(default=None, max_length=100)
    market: str | None = Field(default="中国大陆", max_length=100)


class ProductConfirmRequest(StrictModel):
    acknowledge_unverified: bool = False


class ColorRegion(StrictModel):
    label: str = Field(min_length=1, max_length=60)
    box: list[float] = Field(min_length=4, max_length=4)


class ColorRegionsRequest(StrictModel):
    regions: list[ColorRegion] = Field(min_length=1, max_length=16)


class ClarificationReply(StrictModel):
    answer: str = Field(min_length=1, max_length=1000)


class ProfilePatch(StrictModel):
    skill_level: Literal["beginner", "intermediate", "advanced"] | None = None
    skin_tendency: str | None = None
    preferences: list[str] | None = None
    sensitivities: list[str] | None = None
    available_tools: list[str] | None = None


class FeatureChoicesRequest(StrictModel):
    photo_version: str
    choices: dict[str, str]
    decision: Literal["confirmed", "corrected"]


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    qwen_configured: bool


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'"))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


def create_app(
    *,
    store_path: Path = DEFAULT_STORE_PATH,
    frontend_dist: Path | None = DEFAULT_FRONTEND_DIST,
    task_budget: TaskBudget | None = None,
) -> FastAPI:
    store = ProductStore(store_path)
    manager = TaskManager(store.db)
    resolved_task_budget = task_budget or load_task_budget()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        manager.shutdown()

    app = FastAPI(
        title="妆译 API",
        description="把参考妆面转成结合用户条件和已有产品的可执行教程。",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store_write_lock = threading.Lock()
    step_image_lock = threading.RLock()
    jobs_root = store_path.parent / "jobs"
    app.state.task_manager = manager

    def photo_record():
        item = store.db.get('profile_photo', 'user') or {}
        for field in ('analysis_task_id', 'base_task_id'):
            if item.get(field):
                try:
                    item[field.replace('_id', '')] = manager.get(item[field]).model_dump(mode='json')
                except KeyError:
                    pass
        return item

    def profile_version():
        return (store.db.get('profile_photo', 'user') or {}).get('version')

    def start_profile_job(version: str, kind: str):
        from .facial_images import analysis_worker, base_worker
        item = store.db.get('profile_photo', 'user')
        if not item or item['version'] != version:
            raise ValueError('照片已变更')
        directory = jobs_root / f'portrait-{version}'
        source = directory / item['filename']
        if kind == 'base_image':
            staging_dir = directory / ('stage-' + __import__('uuid').uuid4().hex[:10])
            staging_dir.mkdir()
            staging = staging_dir / 'base.png'
            args = (str(source), str(staging))
            worker = base_worker
        else:
            staging = None
            staging_dir = None
            args = (str(source), item['mime'])
            worker = analysis_worker

        def persist(payload):
            if profile_version() != version:
                raise ValueError('照片已变更，旧结果已丢弃')
            current = store.db.get('profile_photo', 'user')
            if kind == 'profile_analysis':
                current['observations'] = payload
                current['observation_model'] = os.getenv('QWEN_VISION_MODEL', 'qwen3-vl-plus')
                from .face_profile import ANALYSIS_PROMPT_VERSION
                current['analysis_prompt_version'] = ANALYSIS_PROMPT_VERSION
            else:
                staged = Path(payload['staged_path'])
                final = directory / 'standard.png'
                staged.replace(final)
                current['base_url'] = f'/api/media/portrait-{version}/standard.png'
                current['base_model'] = payload['model']
                current['crop_boxes'] = payload['crop_boxes']
            store.db.put('profile_photo', 'user', current)
            from .facial_images import PROMPT_VERSION
            store.db.put('photo_artifact', version, {**{key: current.get(key) for key in
                ('observations', 'observation_model', 'analysis_prompt_version',
                 'base_url', 'base_model', 'crop_boxes')},
                'prompt_version': PROMPT_VERSION})
            if kind == 'base_image':
                for tutorial in store.db.list('tutorial'):
                    if tutorial.get('photo_version') == version:
                        threading.Thread(target=schedule_tutorial_images, args=(tutorial['id'],), daemon=True).start()
            return {key: value for key, value in payload.items() if key != 'staged_path'}

        try:
            task = manager.submit(kind=kind, worker=worker, args=args,
                target_id=version, timeout_seconds=240 if kind == 'base_image' else 75,
                foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
                on_success=persist, cleanup_path=staging_dir)
        except Exception:
            if staging_dir:
                shutil.rmtree(staging_dir, ignore_errors=True)
            raise
        item = store.db.get('profile_photo', 'user')
        item[kind.replace('profile_analysis', 'analysis').replace('base_image', 'base') + '_task_id'] = task.id
        if kind == 'base_image':
            item.pop('base_error', None)
        store.db.put('profile_photo', 'user', item)
        return task

    async def read_photo(upload):
        import io
        from PIL import Image, UnidentifiedImageError
        content = await upload.read(MAX_IMAGE_BYTES + 1)
        await upload.close()
        if not content or len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(413, '图片为空或超过 12 MB')
        try:
            with Image.open(io.BytesIO(content)) as image:
                # Some iPhone .JPG files include extra MPF frames. Pillow reports
                # these JPEG-backed files as MPO even though the primary photo is JPEG.
                if image.format not in {'JPEG', 'PNG', 'WEBP', 'MPO'}:
                    raise ValueError('不支持的图片')
                if image.format == 'MPO' and not content.startswith(b'\xff\xd8\xff'):
                    raise ValueError('MPO 主图不是 JPEG')
                if image.width * image.height > 100_000_000:
                    raise HTTPException(413, '图片像素过大，请先缩小到 1 亿像素以内')
                mime = 'image/jpeg' if image.format == 'MPO' else Image.MIME[image.format]
                image.verify()
        except (UnidentifiedImageError, ValueError, OSError) as exc:
            raise HTTPException(415, '请上传有效的 JPG、PNG 或 WEBP 图片') from exc
        return content, mime

    @app.post('/api/photos/recognize')
    async def recognize_product(images: list[UploadFile] = File()):
        from .photo import observe_photos
        from starlette.concurrency import run_in_threadpool
        if not 1 <= len(images) <= 2:
            raise HTTPException(422, '最多上传包装正面和色号标签两张图片')
        photos = [await read_photo(image) for image in images]
        try:
            return await run_in_threadpool(observe_photos, photos, 'product')
        except Exception as exc:
            raise _http_error(exc)

    @app.post('/api/products/{product_id}/photos')
    async def product_photos(product_id: str, images: list[UploadFile] = File()):
        if not 1 <= len(images) <= 2:
            raise HTTPException(422, '最多两张图片')
        photos = [await read_photo(image) for image in images]
        try:
            with manager._lock, store_write_lock:
                cancel_product_jobs(product_id)
                record = store.reset(product_id)
                directory = jobs_root / product_id
                directory.mkdir(parents=True, exist_ok=True)
                for old_photo in directory.glob('photo-*'):
                    old_photo.unlink()
                urls = []
                for i, (data, mime) in enumerate(photos):
                    filename = f'photo-{i}.' + mime.split('/')[-1]
                    (directory / filename).write_bytes(data)
                    urls.append(f'/api/media/{product_id}/{filename}')
                record.photo_urls = urls
                return store._replace(record)
        except KeyError as exc:
            raise _http_error(exc)

    def start_product_color_job(product_id: str):
        from .makeup_colors import ALGORITHM_VERSION, product_color_worker
        from .models import ColorSample
        record = store.get(product_id)
        if not record.opened_photo_url or not record.opened_photo_version:
            raise ValueError('请先上传打开后的产品照片')
        version = record.opened_photo_version
        model = os.getenv('QWEN_VISION_MODEL', 'qwen3-vl-plus')
        cache_key = hashlib.sha256(f'{product_id}:{version}:product-color-v1:{model}:{ALGORITHM_VERSION}'.encode()).hexdigest()
        cached = store.db.get('product_color_artifact', cache_key)
        if cached:
            record.color_samples = [ColorSample.model_validate(x) for x in cached['samples']]
            record.color_error = None
            record.color_task_id = None
            store._replace(record)
            return None

        path = jobs_root / product_id / Path(record.opened_photo_url).name

        def persist(payload):
            with store_write_lock:
                current = store.get(product_id)
                if current.opened_photo_version != version:
                    raise ValueError('产品照片已变更，旧颜色结果已丢弃')
                current.color_samples = [ColorSample.model_validate(x) for x in payload['samples']]
                current.color_error = None
                store._replace(current)
                store.db.put('product_color_artifact', cache_key, {**payload, 'product_id': product_id, 'version': version})
            return payload

        task = manager.submit(kind='product_color', worker=product_color_worker,
            args=(str(path),), target_id=product_id, timeout_seconds=75,
            foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
            on_success=persist)
        record.color_task_id = task.id
        record.color_error = None
        store._replace(record)
        return task

    def clear_product_color_cache(product_id: str, keep_version: str | None = None):
        with store.db.connect() as db:
            rows = db.execute("SELECT id,payload FROM documents WHERE kind='product_color_artifact'").fetchall()
            for artifact_id, raw in rows:
                artifact = json.loads(raw)
                if artifact.get('product_id') == product_id and artifact.get('version') != keep_version:
                    db.execute("DELETE FROM documents WHERE kind='product_color_artifact' AND id=?", (artifact_id,))

    @app.post('/api/products/{product_id}/photos/{role}')
    async def product_photo_by_role(product_id: str, role: Literal['package', 'opened'], image: UploadFile = File()):
        data, mime = await read_photo(image)
        version = hashlib.sha256(data).hexdigest()
        try:
            with manager._lock, store_write_lock:
                record = store.get(product_id)
                directory = jobs_root / product_id
                directory.mkdir(parents=True, exist_ok=True)
                suffix = '.jpg' if mime == 'image/jpeg' else '.png' if mime == 'image/png' else '.webp'
                filename = f'{role}-{version[:20]}{suffix}'
                path = directory / filename
                if not path.exists():
                    path.write_bytes(data)
                url = f'/api/media/{product_id}/{filename}'
                if role == 'opened':
                    old_url = record.opened_photo_url
                    for task in manager.list():
                        if task.target_id == product_id and task.kind == 'product_color' and task.cancellable:
                            manager.cancel(task.id)
                    if record.opened_photo_version != version:
                        record.color_samples = []
                        record.color_task_id = None
                        record.color_error = None
                    record.opened_photo_url = url
                    record.opened_photo_version = version
                else:
                    old_url = record.photo_urls[0] if record.photo_urls else None
                    for task in manager.list():
                        if task.target_id == product_id and task.kind == 'product_enrichment' and task.cancellable:
                            manager.cancel(task.id)
                    record.photo_urls = [url]
                if role == 'package':
                    record.updated_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
                store._replace(record)
                task = start_product_color_job(product_id) if role == 'opened' and not record.color_samples else None
                if old_url and old_url != url:
                    (directory / Path(old_url).name).unlink(missing_ok=True)
                    if role == 'opened':
                        clear_product_color_cache(product_id, version)
                return {'product': store.get(product_id).model_dump(mode='json'),
                        'task': task.model_dump(mode='json') if task else None}
        except (KeyError, RuntimeError, ValueError) as exc:
            raise _http_error(exc)

    @app.post('/api/products/{product_id}/colors/analyze')
    def retry_product_color(product_id: str):
        try:
            with manager._lock, store_write_lock:
                task = start_product_color_job(product_id)
                return {'product': store.get(product_id).model_dump(mode='json'),
                        'task': task.model_dump(mode='json') if task else None}
        except (KeyError, RuntimeError, ValueError) as exc:
            raise _http_error(exc)

    @app.put('/api/products/{product_id}/colors/regions', response_model=ProductRecord)
    def correct_product_color_regions(product_id: str, request: ColorRegionsRequest):
        from .makeup_colors import extract_product_samples
        try:
            with manager._lock, store_write_lock:
                record = store.get(product_id)
                if not record.opened_photo_url:
                    raise ValueError('请先上传打开后的产品照片')
                for task in manager.list():
                    if task.target_id == product_id and task.kind == 'product_color' and task.cancellable:
                        manager.cancel(task.id)
                path = jobs_root / product_id / Path(record.opened_photo_url).name
                samples = extract_product_samples(path, [item.model_dump() for item in request.regions])
                if len(samples) != len(request.regions):
                    raise ValueError('部分色块取色失败，请避开反光或重新选择区域')
                record.color_samples = samples
                record.color_task_id = None
                record.color_error = None
                return store._replace(record)
        except (KeyError, ValueError, OSError) as exc:
            raise _http_error(exc)

    @app.delete('/api/products/{product_id}/photos/{role}')
    def delete_product_photo_by_role(product_id: str, role: Literal['package', 'opened']):
        try:
            with manager._lock, store_write_lock:
                record = store.get(product_id)
                if role == 'opened':
                    for task in manager.list():
                        if task.target_id == product_id and task.kind == 'product_color' and task.cancellable:
                            manager.cancel(task.id)
                    old = record.opened_photo_url
                    record.opened_photo_url = None
                    record.opened_photo_version = None
                    record.color_samples = []
                    record.color_task_id = None
                    record.color_error = None
                else:
                    old = record.photo_urls[0] if record.photo_urls else None
                    record.photo_urls = []
                store._replace(record)
                if old:
                    (jobs_root / product_id / Path(old).name).unlink(missing_ok=True)
                if role == 'opened':
                    clear_product_color_cache(product_id)
                return record
        except KeyError as exc:
            raise _http_error(exc)

    @app.get('/api/profile/photo')
    def profile_photo():
        return photo_record()

    @app.post('/api/profile/photo')
    async def save_profile_photo(image: UploadFile = File()):
        data, mime = await read_photo(image)
        from PIL import Image, ImageOps, UnidentifiedImageError
        import io
        try:
            with Image.open(io.BytesIO(data)) as preview:
                if min(preview.size) < 500:
                    raise HTTPException(422, '照片分辨率不足，请上传至少 500 像素的正面照')
                preview.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                oriented = ImageOps.exif_transpose(preview)
                normalized = io.BytesIO()
                oriented.convert('RGB').save(normalized, format='JPEG', quality=92)
                processed = normalized.getvalue()
        except (UnidentifiedImageError, ValueError, OSError) as exc:
            raise HTTPException(415, '图片无法完整解码，请重新导出 JPG、PNG 或 WEBP 图片') from exc
        digest = hashlib.sha256(data).hexdigest()[:20]
        data, mime = processed, 'image/jpeg'
        try:
            old = store.db.get('profile_photo', 'user')
            if old and old.get('version') == digest:
                return photo_record()
            directory = jobs_root / f'portrait-{digest}'
            directory.mkdir(parents=True, exist_ok=True)
            filename = 'portrait.' + mime.split('/')[-1].replace('jpeg', 'jpg')
            (directory / filename).write_bytes(data)
            with manager._lock:
                if old:
                    for field in ('analysis_task_id', 'base_task_id'):
                        if old.get(field):
                            manager.cancel(old[field])
                    for active in manager.list():
                        if active.kind == 'step_image' and active.cancellable:
                            manager.cancel(active.id)
                cached = store.db.get('photo_artifact', digest) or {}
                from .facial_images import PROMPT_VERSION, image_model
                if cached.get('prompt_version') != PROMPT_VERSION or cached.get('base_model') != image_model():
                    cached.pop('base_url', None)
                    cached.pop('crop_boxes', None)
                if cached.get('observation_model') != os.getenv('QWEN_VISION_MODEL', 'qwen3-vl-plus'):
                    cached.pop('observations', None)
                from .face_profile import ANALYSIS_PROMPT_VERSION
                if cached.get('analysis_prompt_version') != ANALYSIS_PROMPT_VERSION:
                    cached.pop('observations', None)
                if not (directory / 'standard.png').is_file():
                    cached.pop('base_url', None)
                    cached.pop('crop_boxes', None)
                record = {
                    'version': digest, 'mime': mime, 'filename': filename,
                    'image_url': f'/api/media/portrait-{digest}/{filename}',
                    'observations': cached.get('observations'),
                    'analysis_prompt_version': cached.get('analysis_prompt_version'),
                    'base_url': cached.get('base_url'),
                    'base_model': cached.get('base_model'), 'crop_boxes': cached.get('crop_boxes'),
                }
                store.db.put('profile_photo', 'user', record)
                if not record['observations']:
                    start_profile_job(digest, 'profile_analysis')
                if not record['base_url']:
                    try:
                        start_profile_job(digest, 'base_image')
                    except Exception as exc:
                        item = store.db.get('profile_photo', 'user')
                        item['base_error'] = str(exc)
                        store.db.put('profile_photo', 'user', item)
            return photo_record()
        except Exception as exc:
            raise _http_error(exc)

    @app.post('/api/profile/photo/{kind}/retry', status_code=202)
    def retry_profile_photo(kind: str):
        if kind not in ('profile_analysis', 'base_image'):
            raise HTTPException(404, '未知任务')
        version = profile_version()
        if not version:
            raise HTTPException(404, '没有照片')
        with manager._lock:
            item = store.db.get('profile_photo', 'user')
            task_id = item.get('analysis_task_id' if kind == 'profile_analysis' else 'base_task_id')
            if task_id:
                manager.cancel(task_id)
            return start_profile_job(version, kind)

    @app.delete('/api/profile/photo')
    def delete_profile_photo():
        current = store.db.get('profile_photo', 'user') or {}
        with manager._lock:
            for task in manager.list():
                if task.kind in {'profile_analysis', 'base_image', 'step_image'} and task.cancellable:
                    manager.cancel(task.id)
            store.db.delete('profile_photo', 'user')
            saved_profile = store.db.get('profile', 'user')
            if saved_profile:
                saved_profile.update(face_feature_choices={}, face_feature_decisions={},
                                     face_feature_photo_version=None, face_features_effective={})
                store.db.put('profile', 'user', saved_profile)
            for item in store.db.list('tutorial'):
                if item.get('photo_version'):
                    for record in store.db.list('step_image'):
                        if record.get('tutorial_id') == item['id']:
                            store.db.delete('step_image', record['id'])
                    item['photo_deleted'] = True
                    store.db.put('tutorial', item['id'], item)
        with store.db.connect() as db:
            db.execute("DELETE FROM documents WHERE kind='photo_observation'")
            db.execute("DELETE FROM documents WHERE kind='photo_artifact'")
        for directory in jobs_root.glob('portrait-*'):
            shutil.rmtree(directory, ignore_errors=True)
        shutil.rmtree(jobs_root / 'profile', ignore_errors=True)
        for directory in jobs_root.glob('step-*'):
            shutil.rmtree(directory, ignore_errors=True)
        return {'deleted': True}

    @app.get('/api/profile')
    def get_profile():
        return store.db.get('profile', 'user') or UserProfile().model_dump(mode='json')

    @app.get('/api/profile/feature-options')
    def profile_feature_options():
        from .face_profile import schema
        return schema()

    @app.put('/api/profile')
    def save_profile(profile: UserProfile):
        store.db.put('profile', 'user', profile.model_dump(mode='json'))
        return profile

    @app.patch('/api/profile')
    def patch_profile(patch: ProfilePatch):
        changes = patch.model_dump(exclude_unset=True)
        if not changes:
            raise HTTPException(422, '没有需要保存的资料')
        with store_write_lock:
            current = get_profile()
            current.update(changes)
            saved = UserProfile.model_validate(current).model_dump(mode='json')
            store.db.put('profile', 'user', saved)
        return saved

    @app.put('/api/profile/features')
    def save_profile_features(request: FeatureChoicesRequest):
        from .face_profile import FEATURES
        if not request.choices:
            raise HTTPException(422, '请选择至少一项面部特点')
        with store_write_lock:
            photo = store.db.get('profile_photo', 'user') or {}
            observed = (photo.get('observations') or {}).get('features')
            if not photo.get('version') or not isinstance(observed, dict):
                raise HTTPException(409, '请先完成新版面部分析')
            if request.photo_version != photo['version']:
                raise HTTPException(409, '照片已更换，请刷新后重新确认')
            for key, value in request.choices.items():
                if key not in FEATURES or value not in FEATURES[key]['options']:
                    raise HTTPException(422, f'未知面部选项：{key}={value}')
                if request.decision == 'confirmed' and value != (observed.get(key) or {}).get('value'):
                    raise HTTPException(409, 'AI 结果已更新，请刷新后重新确认')
            current = get_profile()
            if current.get('face_feature_photo_version') != photo['version']:
                current.update(face_feature_choices={}, face_feature_decisions={})
            choices = dict(current.get('face_feature_choices') or {})
            decisions = dict(current.get('face_feature_decisions') or {})
            choices.update(request.choices)
            decisions.update({key: request.decision for key in request.choices})
            current.update(face_feature_choices=choices, face_feature_decisions=decisions,
                           face_feature_photo_version=photo['version'])
            saved = UserProfile.model_validate(current).model_dump(mode='json')
            store.db.put('profile', 'user', saved)
        return saved

    @app.delete('/api/profile')
    def delete_profile():
        delete_profile_photo()
        store.db.delete('profile', 'user')
        return {'deleted': True}

    def cancel_product_jobs(product_id):
        for task in manager.list():
            if task.target_id == product_id and task.cancellable:
                manager.cancel(task.id)

    @app.delete('/api/products/{product_id}')
    def delete_product(product_id: str):
        try:
            with manager._lock:
                cancel_product_jobs(product_id)
                with store_write_lock:
                    store.delete(product_id)
                    store.db.delete('product_refresh_candidate', product_id)
                    clear_product_color_cache(product_id)
                    shutil.rmtree(jobs_root / product_id, ignore_errors=True)
            return {'deleted': True}
        except KeyError as exc:
            raise _http_error(exc)

    @app.put('/api/products/{product_id}')
    def edit_product(product_id: str, request: ProductCreateRequest):
        try:
            with manager._lock:
                cancel_product_jobs(product_id)
                with store_write_lock:
                    store.db.delete('product_refresh_candidate', product_id)
                    return store.reset(product_id, {'raw_brand': request.brand.strip(), 'raw_name': request.name.strip(), 'raw_model_or_shade': request.model_or_shade, 'raw_market': request.market})
        except (KeyError, ValueError) as exc:
            raise _http_error(exc)

    @app.post('/api/products/{product_id}/reset')
    def reset_product(product_id: str):
        try:
            with manager._lock:
                cancel_product_jobs(product_id)
                with store_write_lock:
                    store.db.delete('product_refresh_candidate', product_id)
                    return store.reset(product_id)
        except (KeyError, ValueError) as exc:
            raise _http_error(exc)

    @app.post('/api/products/batch/enrich')
    def batch_enrich(ids: list[str]):
        results, errors = [], {}
        for product_id in dict.fromkeys(ids):
            try:
                results.append(enrich_product(product_id))
            except HTTPException as exc:
                errors[product_id] = exc.detail
        return {'tasks': results, 'errors': errors}

    @app.get('/api/tutorials')
    def list_tutorials():
        return [tutorial_with_task(item) for item in store.db.list('tutorial')]

    def tutorial_stage_task(item: dict, stage: str):
        task_id = item.get(f'{stage}_task_id')
        if not task_id:
            if item.get(f'{stage}_launch_error'):
                from datetime import datetime, timezone
                created = item.get('created_at') or datetime.now(timezone.utc).isoformat()
                return TaskSnapshot(id=f"{item['id']}-{stage}-launch", kind='tutorial_' + stage,
                    status='failed', progress=0, stage='launch_failed', message='启动失败',
                    target_id=item['id'], error=item[f'{stage}_launch_error'],
                    created_at=created, updated_at=created, elapsed_seconds=0,
                    foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
                    timeout_seconds=resolved_task_budget.tutorial_timeout_seconds).model_dump(mode='json')
            return None
        try:
            return manager.get(task_id).model_dump(mode='json')
        except KeyError:
            return None

    def tutorial_steps_result(item: dict):
        if item.get('workflow_version') != 2:
            task = manager.get(item['id'])
        else:
            task_id = item.get('steps_task_id')
            if not task_id:
                return None
            task = manager.get(task_id)
        return task.result if task.status == 'succeeded' else None

    def launch_tutorial_branch(tutorial_id: str, stage: str):
        item = store.db.get('tutorial', tutorial_id)
        if not item or not item.get('shared_analysis'):
            raise ValueError('共享分析尚未完成')
        from uuid import uuid4
        task_id = 'task-' + uuid4().hex[:14]
        image = jobs_root / tutorial_id / Path(item['image_url']).name
        if stage == 'steps':
            worker, args = tutorial_steps_worker, (
                str(image), item['profile'], item['provider'], item['shared_analysis'])
            callback = lambda result: tutorial_finished(tutorial_id, result)
        elif stage == 'products':
            worker, args = tutorial_products_worker, (item['shared_analysis'], item['products'])
            callback = None
        else:
            raise ValueError('未知教程阶段')
        task = manager.submit(task_id=task_id, kind='tutorial_' + stage,
            worker=worker, args=args, target_id=tutorial_id,
            timeout_seconds=resolved_task_budget.tutorial_timeout_seconds,
            foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
            on_success=callback)
        item[f'{stage}_task_id'] = task.id
        item.pop(f'{stage}_launch_error', None)
        store.db.put('tutorial', tutorial_id, item)
        return task

    def analysis_finished(tutorial_id: str, shared: dict):
        item = store.db.get('tutorial', tutorial_id)
        if not item:
            raise ValueError('教程已删除，分析结果不再保存')
        item['shared_analysis'] = shared
        store.db.put('tutorial', tutorial_id, item)
        for stage in ('steps', 'products'):
            try:
                launch_tutorial_branch(tutorial_id, stage)
            except Exception as exc:
                item = store.db.get('tutorial', tutorial_id)
                item[f'{stage}_launch_error'] = str(exc)
                store.db.put('tutorial', tutorial_id, item)
        return shared

    def tutorial_with_task(item: dict):
        if item.get('workflow_version') == 2:
            analysis = tutorial_stage_task(item, 'analysis')
            steps = tutorial_stage_task(item, 'steps')
            products = tutorial_stage_task(item, 'products')
            if not analysis:
                raise HTTPException(500, '教程分析任务记录缺失')
            step_result = steps.get('result') if steps and steps['status'] == 'succeeded' else None
            product_result = products.get('result') if products and products['status'] == 'succeeded' else None
            if step_result and len(step_result.get('step_illustrations') or []) != len(step_result['tutorial']['steps']):
                from .illustrations import attach_illustrations
                from .models import PipelineResult
                step_result = attach_illustrations(PipelineResult.model_validate(step_result)).model_dump(mode='json')
            if step_result and product_result:
                step_result = json.loads(json.dumps(step_result))
                by_key = {(row['target_id'], row['card_id'], row['category']): row['match']
                          for row in product_result.get('items', [])}
                for step in step_result['tutorial']['steps']:
                    key = (step.get('target_id'), step.get('technique_card_id'),
                           step['product_match']['required_category'])
                    if key in by_key:
                        step['product_match'] = by_key[key]
                    elif step['product_match']['status'] == 'pending':
                        step['product_match'].update(status='missing',
                            match_reason='产品清单已完成，本步骤暂无用途和技法都相符的候选')
            return dict(item, task=analysis, stages={'analysis': analysis, 'steps': steps,
                'products': products}, analysis_result=(item.get('shared_analysis') or {}).get('analysis'),
                steps_result=step_result, products_result=product_result,
                step_images=[step_image_view(record) for record in store.db.list('step_image')
                             if record.get('tutorial_id') == item['id']])
        try:
            task = manager.get(item['id'])
        except KeyError:
            from datetime import datetime, timezone

            created_at = item.get('created_at') or datetime.now(timezone.utc).isoformat()
            task = TaskSnapshot(
                id=item['id'],
                kind='tutorial',
                status='failed',
                progress=0,
                stage='interrupted',
                message='历史任务记录不完整，可手动重试',
                error='服务曾在任务登记完成前中断',
                created_at=created_at,
                updated_at=created_at,
                elapsed_seconds=0,
                foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
                timeout_seconds=resolved_task_budget.tutorial_timeout_seconds,
            )
        snapshot = task.model_dump(mode='json')
        if snapshot.get('result') and len(snapshot['result'].get('step_illustrations') or []) != len(snapshot['result']['tutorial']['steps']):
            from .illustrations import attach_illustrations
            from .models import PipelineResult
            snapshot['result'] = attach_illustrations(PipelineResult.model_validate(snapshot['result'])).model_dump(mode='json')
        return dict(item, task=snapshot, step_images=[step_image_view(record)
            for record in store.db.list('step_image') if record.get('tutorial_id') == item['id']])

    def step_image_view(record):
        result = dict(record)
        if record.get('source') == 'ai_effect':
            result.update(source='svg', status='unavailable', image_url=None,
                          reason='旧版步骤图可能改变面部结构，请手动重新生成')
            return result
        if record.get('task_id'):
            try:
                task = manager.get(record['task_id'])
                result['status'] = task.status
                result['error'] = task.error
            except KeyError:
                result['status'] = 'failed'
        return result

    def step_part(step):
        from .facial_images import REGION_PARTS
        drawing = step.get('drawing') or {}
        anchor = drawing.get('anchor')
        if anchor in {'left_eye', 'right_eye', 'left_cheek', 'right_cheek', 'left_brow', 'right_brow'}:
            if drawing.get('side') == 'both':
                return {'eye': 'eyes', 'cheek': 'cheeks', 'brow': 'brows'}[anchor.split('_')[1]]
            return anchor
        if step.get('technique_card_id') == 'nose-soft-contour':
            return 'nose'
        parts = REGION_PARTS.get(step['region'], ('face',))
        wording = str(step.get('instruction', '')) + str(step.get('goal', ''))
        return parts[-1] if len(parts) == 2 and '右' in wording and '左' not in wording else parts[0]

    def step_effects(result, step, reference_path):
        from .makeup_colors import sample_surrounding_color
        targets = {t['id']: t for t in result['analysis'].get('targets', [])}
        history = []
        for previous in result['tutorial']['steps']:
            if previous['order'] > step['order']:
                continue
            if previous.get('product_match', {}).get('status') == 'not_needed':
                continue
            target = targets.get(previous.get('target_id'), {})
            samples = target.get('color_samples') or []
            sample = next((s for s in samples if s.get('source') == 'reference_photo'
                           and s.get('confidence') in ('high', 'medium') and s.get('hex')), None)
            surrounding = None
            if sample and sample.get('box') and reference_path.is_file():
                try:
                    surrounding = sample_surrounding_color(reference_path, sample['box'])
                except (OSError, TypeError, ValueError):
                    pass
            history.append({'region': previous['region'], 'role': target.get('role') or previous.get('target_role'),
                'card_id': previous.get('technique_card_id'), 'goal': previous.get('goal'),
                'intensity': target.get('intensity', 'light'),
                'color_hex': sample['hex'] if sample else None,
                'reference_skin_hex': surrounding,
                'target_id': previous.get('target_id'),
                'drawing': previous.get('drawing')})
        return history

    def _enqueue_step_image(tutorial_id, order, edited, retry=False):
        from .facial_images import STEP_RENDER_VERSION, baseline_step, digest_payload, step_worker
        item = store.db.get('tutorial', tutorial_id)
        if not item:
            raise KeyError('教程不存在')
        steps_result = tutorial_steps_result(item)
        if not steps_result:
            raise ValueError('文字教程尚未完成')
        step = next((s for s in steps_result['tutorial']['steps'] if s['order'] == order), None)
        if not step:
            raise KeyError('步骤不存在')
        version = item.get('photo_version')
        if version and version != profile_version():
            raise ValueError('教程使用的是旧照片，不能对已更换的照片重新生成步骤图')
        base = jobs_root / f'portrait-{version}' / 'standard.png'
        record_id = f'{tutorial_id}-{order}'
        if not version or item.get('photo_deleted') or not base.is_file():
            return {'id': record_id, 'tutorial_id': tutorial_id, 'step_order': order,
                    'source': 'svg', 'status': 'unavailable', 'reason': '未提供可用标准底图'}
        part = step_part(step)
        effects = step_effects(steps_result, step, jobs_root / tutorial_id / 'reference.jpg')
        key = digest_payload(version, part, effects, STEP_RENDER_VERSION)
        existing = store.db.get('step_image', record_id)
        if existing and existing.get('source') == 'ai_effect' and not retry:
            return step_image_view(existing)
        if existing and existing.get('cache_key') == key:
            view = step_image_view(existing)
            if view.get('status') in ('failed', 'cancelled', 'timed_out', 'unavailable') and not retry:
                return view
            if view.get('image_url') and (not edited or view.get('source') == 'local_effect'):
                return view
            if view.get('status') in ('queued', 'running'):
                return view
        directory = jobs_root / f'step-{tutorial_id}-{order}'
        directory.mkdir(parents=True, exist_ok=True)
        baseline = directory / 'baseline.png'
        current_effect = effects[-1] if step.get('product_match', {}).get('status') != 'not_needed' and effects else None
        try:
            metadata = baseline_step(base, part, baseline,
                                     effects[:-1] if current_effect else effects, current_effect)
        except ValueError as exc:
            record = {'id': record_id, 'tutorial_id': tutorial_id, 'step_order': order,
                'photo_version': version, 'source': 'svg', 'status': 'unavailable',
                'reason': f'绘图参数无法表达此步骤：{exc}；请以文字和 SVG 示意为准',
                'cache_key': key, 'render_version': STEP_RENDER_VERSION}
            store.db.put('step_image', record_id, record)
            return record
        record = {'id': record_id, 'tutorial_id': tutorial_id, 'step_order': order,
            'photo_version': version, 'source': 'standard_crop', 'status': 'succeeded',
            'image_url': f'/api/media/{directory.name}/baseline.png', 'cache_key': key,
            'render_version': STEP_RENDER_VERSION, **metadata}
        if not edited:
            store.db.put('step_image', record_id, record)
            return record
        staging_dir = directory / ('stage-' + __import__('uuid').uuid4().hex[:10])
        staging_dir.mkdir()
        staging = staging_dir / 'edit.png'

        def persist(payload):
            if not store.db.get('tutorial', tutorial_id) or not base.is_file() or profile_version() != version:
                raise ValueError('照片或教程已变更，旧步骤图已丢弃')
            current = store.db.get('step_image', record_id)
            if not current or current.get('cache_key') != key:
                raise ValueError('步骤图已被更新')
            final = directory / 'edited.png'
            Path(payload['staged_path']).replace(final)
            current.update(source='local_effect', status='succeeded',
                image_url=f'/api/media/{directory.name}/edited.png', box=payload['box'])
            store.db.put('step_image', record_id, current)
            return {key: value for key, value in payload.items() if key != 'staged_path'}

        record.update(status='queued')
        store.db.put('step_image', record_id, record)
        try:
            image_task = manager.submit(kind='step_image', worker=step_worker,
                args=(str(base), part, effects, str(staging)), target_id=record_id,
                timeout_seconds=240, foreground_wait_seconds=8, on_success=persist,
                cleanup_path=staging_dir)
            record['task_id'] = image_task.id
            store.db.put('step_image', record_id, record)
            return step_image_view(record)
        except Exception:
            shutil.rmtree(staging_dir, ignore_errors=True)
            record.update(status='failed', image_url=f'/api/media/{directory.name}/baseline.png')
            store.db.put('step_image', record_id, record)
            raise

    def enqueue_step_image(tutorial_id, order, edited, retry=False):
        with step_image_lock:
            return _enqueue_step_image(tutorial_id, order, edited, retry)

    def schedule_tutorial_images(tutorial_id):
        try:
            item = store.db.get('tutorial', tutorial_id)
            if not item or item.get('photo_deleted') or item.get('photo_version') != profile_version():
                return
            result = tutorial_steps_result(item)
            if not result:
                return
            steps = result['tutorial']['steps']
            selected = _key_edit_orders(steps)
            for step in steps:
                try:
                    enqueue_step_image(tutorial_id, step['order'], step['order'] in selected)
                except Exception:
                    continue
        except (KeyError, ValueError):
            return

    def tutorial_finished(tutorial_id, result):
        threading.Thread(target=schedule_tutorial_images, args=(tutorial_id,), daemon=True).start()
        return result

    @app.get('/api/tutorials/{tutorial_id}/images')
    def get_tutorial_images(tutorial_id: str):
        if not store.db.get('tutorial', tutorial_id):
            raise HTTPException(404, '教程不存在')
        schedule_tutorial_images(tutorial_id)
        records = {record['step_order']: step_image_view(record) for record in store.db.list('step_image')
                   if record.get('tutorial_id') == tutorial_id}
        item = store.db.get('tutorial', tutorial_id)
        result = tutorial_steps_result(item)
        if result:
            for step in result['tutorial']['steps']:
                records.setdefault(step['order'], {'step_order': step['order'], 'source': 'svg',
                    'status': 'unavailable', 'reason': '未提供可用标准底图，显示 SVG 示意'})
        return [records[k] for k in sorted(records)]

    @app.post('/api/tutorials/{tutorial_id}/images/{order}', status_code=202)
    def generate_step_image(tutorial_id: str, order: int):
        try:
            return enqueue_step_image(tutorial_id, order, True, retry=True)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise _http_error(exc)

    @app.get('/api/tutorials/{tutorial_id}')
    def get_tutorial(tutorial_id: str):
        item = store.db.get('tutorial', tutorial_id)
        if not item:
            raise HTTPException(404, '教程不存在')
        return tutorial_with_task(item)

    @app.post('/api/tutorials/{tutorial_id}/retry', status_code=202)
    def retry_tutorial(tutorial_id: str, stage: str | None = None):
        from uuid import uuid4
        item = get_tutorial(tutorial_id)
        if item.get('workflow_version') == 2:
            stages = item['stages']
            if stage is None:
                stage = next((name for name in ('analysis', 'steps', 'products')
                              if not stages.get(name) or stages[name]['status'] in
                              {'failed', 'timed_out', 'cancelled'}), None)
            if stage not in ('analysis', 'steps', 'products'):
                raise HTTPException(409, '没有需要重试的阶段')
            current = stages.get(stage)
            if current and current['status'] in {'queued', 'running'}:
                raise HTTPException(409, '该阶段仍在运行')
            if current and current['status'] == 'succeeded':
                raise HTTPException(409, '该阶段已完成，无需重试')
            if stage != 'analysis':
                return launch_tutorial_branch(tutorial_id, stage)
            image = jobs_root / tutorial_id / Path(item['image_url']).name
            task_id = 'task-' + uuid4().hex[:14]
            task = manager.submit(task_id=task_id, kind='tutorial_analysis',
                worker=tutorial_analysis_worker,
                args=(str(image), item['profile'], item['provider'], item['knowledge_cards']),
                target_id=tutorial_id,
                timeout_seconds=resolved_task_budget.tutorial_timeout_seconds,
                foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
                on_success=lambda shared: analysis_finished(tutorial_id, shared))
            saved = store.db.get('tutorial', tutorial_id)
            saved['analysis_task_id'] = task.id
            store.db.put('tutorial', tutorial_id, saved)
            return task
        if item['task']['status'] in {'queued', 'running'}:
            raise HTTPException(409, '任务仍在进行，请先停止')
        new_id = 'task-' + uuid4().hex[:14]
        directory = jobs_root / new_id
        directory.mkdir(parents=True)
        filename = Path(item['image_url']).name
        shutil.copyfile(jobs_root / tutorial_id / filename, directory / filename)
        item.pop('task')
        item.update(id=new_id, image_url=f'/api/media/{new_id}/{filename}')
        try:
            store.db.put('tutorial', new_id, item)
            return manager.submit(task_id=new_id, kind='tutorial', worker=tutorial_worker,
                args=(str(directory / filename), item['profile'], item['products'], item['provider']),
                timeout_seconds=resolved_task_budget.tutorial_timeout_seconds,
                foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
                on_success=lambda result: tutorial_finished(new_id, result))
        except Exception:
            store.db.delete('tutorial', new_id)
            shutil.rmtree(directory, ignore_errors=True)
            raise

    @app.delete('/api/tutorials/{tutorial_id}')
    def delete_tutorial(tutorial_id: str):
        item = get_tutorial(tutorial_id)
        for task_id in (tutorial_id, item.get('analysis_task_id'),
                        item.get('steps_task_id'), item.get('products_task_id')):
            if task_id:
                manager.cancel(task_id)
        for image_task in manager.list():
            if image_task.kind == 'step_image' and image_task.target_id and image_task.target_id.startswith(tutorial_id + '-') and image_task.cancellable:
                manager.cancel(image_task.id)
        for record in store.db.list('step_image'):
            if record.get('tutorial_id') == tutorial_id:
                store.db.delete('step_image', record['id'])
                shutil.rmtree(jobs_root / f"step-{tutorial_id}-{record['step_order']}", ignore_errors=True)
        store.db.delete('tutorial', tutorial_id)
        store.db.delete('follow_session', tutorial_id)
        store.db.delete('feedback', tutorial_id)
        with manager._lock:
            for task_id in (tutorial_id, item.get('analysis_task_id'),
                            item.get('steps_task_id'), item.get('products_task_id')):
                if task_id:
                    manager._tasks.pop(task_id, None)
                    manager._history.pop(task_id, None)
                    store.db.delete('task', task_id)
        shutil.rmtree(jobs_root / tutorial_id, ignore_errors=True)
        return {'deleted': True}

    @app.get('/api/media/{tutorial_id}/{filename}')
    def get_media(tutorial_id: str, filename: str):
        root = jobs_root.resolve()
        path = (root / tutorial_id / filename).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, '图片不存在')
        return FileResponse(path)

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(qwen_configured=bool(os.getenv("DASHSCOPE_API_KEY")))

    @app.get("/api/products", response_model=list[ProductRecord])
    def list_products() -> list[ProductRecord]:
        return store.load().products

    @app.get("/api/products/{product_id}", response_model=ProductRecord)
    def get_product(product_id: str) -> ProductRecord:
        try:
            return store.get(product_id)
        except KeyError as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/products",
        response_model=ProductRecord,
        status_code=status.HTTP_201_CREATED,
    )
    def create_product(request: ProductCreateRequest) -> ProductRecord:
        try:
            with store_write_lock:
                return store.add_draft(
                    brand=request.brand,
                    name=request.name,
                    model_or_shade=request.model_or_shade,
                    market=request.market,
                )
        except ValueError as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/products/{product_id}/enrich",
        response_model=TaskSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def enrich_product(product_id: str, force_refresh: bool = False) -> TaskSnapshot:
        from .tasks import product_refresh_worker, product_continue_worker
        from .product_agent import partial_product_card
        try:
            record = store.get(product_id)
            if record.status.value != "draft" and not (force_refresh and record.status.value in ('confirmed', 'pending_user_review')):
                raise ValueError(
                    f"该状态不能启动核验：{record.status.value}"
                )

            checkpoint = None
            if not force_refresh:
                previous = next((task for task in manager.list() if task.kind == 'product_enrichment'
                    and task.target_id == product_id and task.checkpoint
                    and task.checkpoint.get('product_updated_at') == record.model_dump(mode='json')['updated_at']
                    and task.status in
                    {'failed', 'timed_out', 'cancelled'}), None)
                if previous:
                    checkpoint = previous.checkpoint

            def persist_enrichment(payload: dict) -> dict:
                enrichment = ProductEnrichment.model_validate(payload)
                with store_write_lock:
                    current = store.get(product_id)
                    if current.updated_at != record.updated_at:
                        raise ValueError('产品资料已更新，请重新补全')
                    if current.status.value in ('confirmed', 'pending_user_review'):
                        store.db.put('product_refresh_candidate', product_id,
                            {'product_updated_at': str(current.updated_at),
                             'enrichment': enrichment.model_dump(mode='json')})
                        return {'refresh_candidate': enrichment.model_dump(mode='json'),
                                'product_updated_at': current.model_dump(mode='json')['updated_at']}
                    saved = store.set_pending(product_id, enrichment)
                    return saved.model_dump(mode="json")

            photo_paths = [str(jobs_root / product_id / Path(url).name) for url in record.photo_urls]
            worker = product_continue_worker if checkpoint else (product_refresh_worker if force_refresh else product_enrichment_worker)
            args = (record.model_dump(mode='json'), photo_paths, checkpoint, force_refresh) if checkpoint else (
                record.model_dump(mode='json'), photo_paths)

            return manager.submit(
                kind="product_enrichment",
                worker=worker,
                args=args,
                target_id=product_id,
                timeout_seconds=(
                    resolved_task_budget.product_enrichment_timeout_seconds
                ),
                foreground_wait_seconds=(
                    resolved_task_budget.foreground_wait_seconds
                ),
                on_success=persist_enrichment,
                on_timeout=lambda saved: partial_product_card(record, saved),
            )
        except (KeyError, RuntimeError, ValueError) as exc:
            raise _http_error(exc) from exc

    @app.post(
        "/api/products/{product_id}/confirm",
        response_model=ProductRecord,
    )
    def confirm_product(
        product_id: str, request: ProductConfirmRequest
    ) -> ProductRecord:
        try:
            with store_write_lock:
                current = store.get(product_id)
                candidate = store.db.get('product_refresh_candidate', product_id)
                if current.status.value in ('confirmed', 'pending_user_review') and candidate:
                    if candidate['product_updated_at'] != str(current.updated_at):
                        raise ValueError('产品已更新，请重新核验')
                    enrichment = ProductEnrichment.model_validate(candidate['enrichment'])
                    issues = enrichment.blocking_source_issues()
                    if issues and not request.acknowledge_unverified:
                        raise ValueError('来源仍需确认：' + '；'.join(issues))
                    payload = current.model_dump()
                    from .product_models import utc_now
                    payload.update(enrichment=enrichment, updated_at=utc_now())
                    saved = store._replace(ProductRecord.model_validate(payload))
                    store.db.delete('product_refresh_candidate', product_id)
                    return saved if saved.status.value == 'confirmed' else store.confirm(product_id,
                        acknowledge_unverified=request.acknowledge_unverified)
                return store.confirm(
                    product_id,
                    acknowledge_unverified=request.acknowledge_unverified,
                )
        except (KeyError, ValueError) as exc:
            raise _http_error(exc) from exc

    @app.get("/api/tasks", response_model=list[TaskSnapshot])
    def list_tasks() -> list[TaskSnapshot]:
        return manager.list()

    @app.get("/api/tasks/{task_id}", response_model=TaskSnapshot)
    def get_task(task_id: str) -> TaskSnapshot:
        try:
            return manager.get(task_id)
        except KeyError as exc:
            raise _http_error(exc) from exc

    @app.delete("/api/tasks/{task_id}", response_model=TaskSnapshot)
    def cancel_task(task_id: str) -> TaskSnapshot:
        try:
            return manager.cancel(task_id)
        except KeyError as exc:
            raise _http_error(exc) from exc

    @app.post('/api/tasks/{task_id}/reply', response_model=TaskSnapshot)
    def reply_task(task_id: str, reply: ClarificationReply):
        from .tasks import product_resume_worker
        try:
            with manager._lock:
                task = manager.get(task_id)
                if task.kind != 'product_enrichment' or task.status != 'waiting_user':
                    raise ValueError('该任务不在等待产品信息')
                record = store.get(task.target_id)
                def persist(payload):
                    with store_write_lock:
                        if store.get(record.id).updated_at != record.updated_at:
                            raise ValueError('产品资料已变更，请重新补全')
                        return store.set_pending(record.id, ProductEnrichment.model_validate(payload)).model_dump(mode='json')
                return manager.resume(task_id, worker=product_resume_worker,
                    args=(record.model_dump(mode='json'), [], task.clarification, reply.answer), on_success=persist)
        except (KeyError, ValueError, RuntimeError) as exc:
            raise _http_error(exc)

    @app.post(
        "/api/tutorials",
        response_model=TaskSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_tutorial(
        image: Annotated[UploadFile, File(description="参考妆面图片")],
        profile_json: Annotated[str, Form()] = "{}",
        provider: Annotated[Literal["fixture", "qwen"], Form()] = "qwen",
    ) -> TaskSnapshot:
        suffix = Path(image.filename or "reference.jpg").suffix.lower()
        if suffix not in ALLOWED_IMAGE_SUFFIXES:
            raise HTTPException(
                status_code=415,
                detail="仅支持 JPG、PNG 和 WEBP 图片。",
            )
        content = await image.read(MAX_IMAGE_BYTES + 1)
        await image.close()
        if not content:
            raise HTTPException(status_code=400, detail="上传的图片为空。")
        if len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="图片不能超过 12 MB。")
        try:
            values = {**get_profile(), **json.loads(profile_json)}
            current_photo = store.db.get('profile_photo', 'user') or {}
            observation = current_photo.get('observations') or {}
            from .face_profile import effective_features, feature_terms
            features = effective_features(observation, values.get('face_feature_choices') or {},
                values.get('face_feature_photo_version'), current_photo.get('version'))
            values['face_features_effective'] = features
            if features:
                values['face_observation'] = '、'.join(feature_terms(features))
                values['eye_notes'] = None
            elif not values.get('face_observation'):
                values['face_observation'] = '；'.join(str(observation.get(key) or '') for key in
                    ('brows', 'eyes', 'nose', 'lips', 'contour', 'visible_skin', 'makeup_advice')) or None
            if not values.get('eye_notes') and not features:
                values['eye_notes'] = observation.get('eye_notes') or None
            profile = UserProfile.model_validate(values)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=f"用户条件格式错误：{exc}") from exc

        products = store.export_confirmed()
        jobs_root.mkdir(parents=True, exist_ok=True)
        from uuid import uuid4
        task_id = 'task-' + uuid4().hex[:14]
        directory = jobs_root / task_id
        directory.mkdir()
        try:
            image_path = directory / f"reference{suffix}"
            image_path.write_bytes(content)
            from datetime import datetime, timezone
            store.db.put('tutorial', task_id, {
                'workflow_version': 2,
                'analysis_task_id': task_id,
                'knowledge_cards': [c.model_dump(mode='json') for c in __import__('makeup_translator.knowledge', fromlist=['TechniqueKnowledgeBase']).TechniqueKnowledgeBase().cards],
                'id': task_id,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'profile': profile.model_dump(mode='json'),
                'products': products,
                'product_records': [p.model_dump(mode='json') for p in store.load().products if p.status.value == 'confirmed'],
                'provider': provider,
                'photo_version': profile_version(),
                'image_url': f'/api/media/{task_id}/{image_path.name}',
            })
            return manager.submit(
                task_id=task_id,
                kind="tutorial_analysis",
                worker=tutorial_analysis_worker,
                args=(
                    str(image_path),
                    profile.model_dump(mode="json"),
                    provider,
                    [c.model_dump(mode='json') for c in __import__('makeup_translator.knowledge', fromlist=['TechniqueKnowledgeBase']).TechniqueKnowledgeBase().cards],
                ),
                timeout_seconds=resolved_task_budget.tutorial_timeout_seconds,
                foreground_wait_seconds=resolved_task_budget.foreground_wait_seconds,
                on_success=lambda result: analysis_finished(task_id, result),

            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            store.db.delete('tutorial', task_id)
            shutil.rmtree(directory, ignore_errors=True)
            raise _http_error(exc) from exc

    if frontend_dist and frontend_dist.exists():
        assets = frontend_dist / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="API route not found")
            frontend_root = frontend_dist.resolve()
            requested = (frontend_root / full_path).resolve()
            if (
                full_path
                and requested.is_relative_to(frontend_root)
                and requested.is_file()
            ):
                return FileResponse(requested)
            return FileResponse(frontend_root / "index.html")

    return app


app = create_app()
