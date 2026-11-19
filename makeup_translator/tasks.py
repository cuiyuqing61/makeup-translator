from __future__ import annotations

import multiprocessing
import queue
import shutil
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field

from .models import OwnedProduct, StrictModel, UserProfile
from .pipeline import MakeupTutorialPipeline
from .product_enrichment import QwenProductEnricher
from .product_models import ProductEnrichment, ProductRecord


TaskKind = Literal["tutorial", "tutorial_analysis", "tutorial_steps", "tutorial_products", "product_enrichment", "product_color", "profile_analysis", "base_image", "step_image"]
TaskStatus = Literal[
    "queued", "running", "waiting_user", "succeeded", "failed", "timed_out", "cancelled"
]
TERMINAL_STATUSES = {"succeeded", "failed", "timed_out", "cancelled"}
DEFAULT_TASK_BUDGET_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "task_budget.json"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskSnapshot(StrictModel):
    trace: list[dict] = Field(default_factory=list)
    checkpoint: dict | None = None
    clarification: dict | None = None
    id: str
    kind: TaskKind
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    stage: str
    message: str
    target_id: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    queue_position: int = 0
    queued_seconds: float = 0
    elapsed_seconds: float = Field(ge=0)
    foreground_wait_seconds: float = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    continued_in_background: bool = False
    cancellable: bool = False


class TaskBudget(StrictModel):
    foreground_wait_seconds: float = Field(default=8, gt=0, le=30)
    product_enrichment_timeout_seconds: float = Field(default=60, gt=1, le=60)
    tutorial_timeout_seconds: float = Field(default=600, gt=5, le=900)


def load_task_budget(path: Path = DEFAULT_TASK_BUDGET_PATH) -> TaskBudget:
    if not path.exists():
        return TaskBudget()
    return TaskBudget.model_validate_json(path.read_text(encoding="utf-8"))


class _TaskState:
    def __init__(
        self,
        *,
        task_id: str,
        kind: TaskKind,
        target_id: str | None,
        foreground_wait_seconds: float,
        timeout_seconds: float,
        on_success: Callable[[dict[str, Any]], dict[str, Any]] | None,
        on_timeout: Callable[[dict | None], dict[str, Any]] | None,
        cleanup_path: Path | None,
    ) -> None:
        now = utc_now()
        self.id = task_id
        self.kind = kind
        self.status: TaskStatus = "queued"
        self.progress = 0
        self.stage = "queued"
        self.message = "任务已进入队列"
        self.target_id = target_id
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.created_at = now
        self.updated_at = now
        self.started_at: datetime | None = None
        self.finished_at: datetime | None = None
        self.started_monotonic: float | None = None
        self.foreground_wait_seconds = foreground_wait_seconds
        self.timeout_seconds = timeout_seconds
        self.on_success = on_success
        self.on_timeout = on_timeout
        self.cleanup_path = cleanup_path
        self.process: multiprocessing.Process | None = None
        self.events: Any = None
        self.trace = []
        self.checkpoint = None
        self.clarification = None

    def snapshot(self) -> TaskSnapshot:
        if self.started_monotonic is None:
            elapsed = 0.0
        elif self.status == 'waiting_user' and self.started_at:
            elapsed = max(0, (self.updated_at - self.started_at).total_seconds())
        elif self.status in TERMINAL_STATUSES and self.finished_at and self.started_at:
            elapsed = max(0.0, (self.finished_at - self.started_at).total_seconds())
        else:
            elapsed = max(0.0, time.monotonic() - self.started_monotonic)
        return TaskSnapshot(
            trace=self.trace,
            checkpoint=self.checkpoint,
            clarification=self.clarification,
            id=self.id,
            kind=self.kind,
            status=self.status,
            progress=self.progress,
            stage=self.stage,
            message=self.message,
            target_id=self.target_id,
            result=self.result,
            error=self.error,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            queued_seconds=max(0, ((self.started_at or utc_now()) - self.created_at).total_seconds()),
            elapsed_seconds=round(elapsed, 1),
            foreground_wait_seconds=self.foreground_wait_seconds,
            timeout_seconds=self.timeout_seconds,
            continued_in_background=(
                self.status in {"queued", "running"}
                and elapsed >= self.foreground_wait_seconds
            ),
            cancellable=self.status in {"queued", "running", "waiting_user"},
        )


def _worker_entry(
    worker: Callable[..., dict[str, Any]],
    args: tuple[Any, ...],
    events: Any,
) -> None:
    def report(progress: int, stage: str, message: str) -> None:
        events.put(
            {
                "type": "progress",
                "progress": max(0, min(99, progress)),
                "stage": stage,
                "message": message,
            }
        )

    report.event = lambda kind, data: events.put({'type': kind, 'data': data})
    try:
        result = worker(*args, report)
        events.put({"type": "result", "result": result})
    except BaseException as exc:
        events.put(
            {
                "type": "error",
                "error": str(exc) or exc.__class__.__name__,
                "error_type": exc.__class__.__name__,
                "traceback": traceback.format_exc(limit=8),
            }
        )


def tutorial_worker(
    image_path: str,
    profile_payload: dict[str, Any],
    products_payload: list[dict[str, Any]],
    provider: str,
    report: Callable[[int, str, str], None],
) -> dict[str, Any]:
    profile = UserProfile.model_validate(profile_payload)
    products = [OwnedProduct.model_validate(item) for item in products_payload]
    pipeline = MakeupTutorialPipeline(provider=provider)
    result = pipeline.run(Path(image_path), profile, products, progress=report)
    return result.model_dump(mode="json")


def tutorial_analysis_worker(image_path, profile_payload, provider, cards_payload, report):
    from .models import Confidence, LookAnalysis, MakeupTarget, TechniqueCard
    from .personalized_tutorial import (_actionable_target, _module_decisions,
                                          run_personalized_tutorial)
    pipeline = MakeupTutorialPipeline(provider=provider)
    pipeline.knowledge.cards = [TechniqueCard.model_validate(c) for c in cards_payload]
    if provider == 'qwen':
        return run_personalized_tutorial(pipeline, Path(image_path),
            UserProfile.model_validate(profile_payload), [], report, phase='analysis')
    report(20, 'image_analysis', '正在分析参考妆面')
    analysis = pipeline.analyze_image(Path(image_path))
    analysis.targets = [MakeupTarget(id=f'fixture-{feature.region.value}-{index}',
        region=feature.region, role={'complexion': 'base', 'eyebrow': 'brow',
        'eye': 'base', 'cheek': 'blush', 'lip': 'lip'}[feature.region.value],
        effect=feature.effect, placement=feature.placement or feature.region.value,
        color_family=feature.color_family, finish=feature.finish,
        intensity=feature.intensity, evidence='固定演示中可见：' + feature.effect,
        confidence=Confidence.medium, required=True) for index, feature in enumerate(analysis.features)]
    analysis.module_decisions = _module_decisions(analysis)
    cards = pipeline.knowledge.retrieve(analysis)
    gaps = [t.id for t in analysis.targets if _actionable_target(t)
            and not pipeline.knowledge.candidates_for_target(t, cards)]
    if gaps:
        raise ValueError('以下妆效目标缺少技法：' + '、'.join(gaps))
    return {'analysis': analysis.model_dump(mode='json'),
            'cards': [c.model_dump(mode='json') for c in cards]}


def tutorial_steps_worker(image_path, profile_payload, provider, shared, report):
    from .models import LookAnalysis, TechniqueCard
    from .personalized_tutorial import run_personalized_tutorial
    from .illustrations import attach_illustrations
    profile = UserProfile.model_validate(profile_payload)
    pipeline = MakeupTutorialPipeline(provider=provider)
    pipeline.knowledge.cards = [TechniqueCard.model_validate(c) for c in shared['cards']]
    if provider == 'qwen':
        result = run_personalized_tutorial(pipeline, Path(image_path), profile, [],
            report, phase='steps', shared=shared)
    else:
        report(65, 'tutorial_planning', '正在编排复刻步骤')
        analysis = LookAnalysis.model_validate(shared['analysis'])
        plan = pipeline.generate_tutorial(analysis, profile, [], pipeline.knowledge.cards)
        for step in plan.steps:
            if step.product_match.status != 'not_needed':
                step.product_match.status = 'pending'
                step.product_match.match_reason = '正在独立匹配产品'
        from .models import PipelineResult
        from .knowledge import attach_technique_sources
        attach_technique_sources(plan, pipeline.knowledge.cards)
        result = attach_illustrations(PipelineResult(analysis=analysis,
            retrieved_card_ids=[c.id for c in pipeline.knowledge.cards], tutorial=plan))
    return result.model_dump(mode='json')


def tutorial_products_worker(shared, products_payload, report):
    from .models import LookAnalysis, TechniqueCard
    from .personalized_tutorial import (_actionable_target, _target_candidates,
                                          _product_match)
    analysis = LookAnalysis.model_validate(shared['analysis'])
    cards = [TechniqueCard.model_validate(c) for c in shared['cards']]
    products = [OwnedProduct.model_validate(p) for p in products_payload]
    rows = []
    active = [t for t in analysis.targets if _actionable_target(t)]
    for index, target in enumerate(active):
        report(10 + int(80 * index / max(1, len(active))), 'product_selection',
               f'正在匹配 {target.effect} 所需产品')
        from .personalized_tutorial import _card_matches_target
        for card in cards:
            if not _card_matches_target(card, target):
                continue
            if card.product_categories == ['none']:
                continue
            candidates = _target_candidates(target, card, products)
            good = next((p for p in candidates['owned'] if p['tier'] in ('photo_close', 'metadata_close')), None)
            choice = good or (candidates['catalog'][0] if candidates['catalog'] else None)
            match = _product_match(target, card, candidates,
                                   {'id': choice['id'] if choice else None})
            rows.append({'target_id': target.id, 'target_effect': target.effect,
                         'card_id': card.id, 'category': match.required_category,
                         'required_product_kinds': card.required_product_kinds,
                         'match': match.model_dump(mode='json')})
    if not rows and active:
        raise ValueError('没有可核验的产品用途要求')
    return {'items': rows, 'review_status': 'passed'}


def product_enrichment_worker(
    record_payload: dict[str, Any],
    photo_paths: list[str],
    report: Callable[[int, str, str], None],
) -> dict[str, Any]:
    record = ProductRecord.model_validate(record_payload)
    from .product_agent import run_product_agent
    return run_product_agent(record, photo_paths, report)


def product_resume_worker(record_payload, photo_paths, clarification, answer, report):
    from .product_agent import run_product_agent
    return run_product_agent(ProductRecord.model_validate(record_payload), photo_paths, report,
        answer=answer, round_number=clarification['round'], checkpoint=clarification['checkpoint'])


def product_refresh_worker(record_payload, photo_paths, report):
    from .product_agent import run_product_agent
    return run_product_agent(ProductRecord.model_validate(record_payload), photo_paths, report, force_refresh=True)


def product_continue_worker(record_payload, photo_paths, checkpoint, force_refresh, report):
    from .product_agent import run_product_agent
    return run_product_agent(ProductRecord.model_validate(record_payload), photo_paths, report,
                             checkpoint=checkpoint, force_refresh=force_refresh)


class TaskManager:
    """Runs blocking model work in killable child processes."""

    def __init__(self, storage=None) -> None:
        self.storage = storage
        self._history = {}
        if storage:
            for item in storage.list('task'):
                if item['status'] in {'queued', 'running'}:
                    item.update(status='failed', stage='interrupted', message='服务重启，任务已中断；可手动重试', error='服务重启，未重新发送请求', cancellable=False)
                    storage.put('task', item['id'], item)
                self._history[item['id']] = TaskSnapshot.model_validate(item)
        self._context = multiprocessing.get_context("spawn")
        self._tasks: dict[str, _TaskState] = {}
        self._lock = threading.RLock()
        self._shutting_down = False

    def submit(
        self,
        *,
        kind: TaskKind,
        worker: Callable[..., dict[str, Any]],
        args: tuple[Any, ...],
        timeout_seconds: float,
        foreground_wait_seconds: float = 8,
        target_id: str | None = None,
        on_success: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        on_timeout: Callable[[dict | None], dict[str, Any]] | None = None,
        cleanup_path: Path | None = None,
        task_id: str | None = None,
    ) -> TaskSnapshot:
        with self._lock:
            if self._shutting_down:
                raise RuntimeError("任务服务正在关闭")
            if target_id and any(
                state.target_id == target_id
                and state.kind == kind
                and state.status not in TERMINAL_STATUSES
                for state in self._tasks.values()
            ) or (target_id and any(s.target_id == target_id and s.kind == kind and s.status == 'waiting_user' for s in self._history.values())):
                raise ValueError("该对象已有正在运行的任务")
            resolved_id = task_id or f"task-{uuid4().hex[:14]}"
            state = _TaskState(
                task_id=resolved_id,
                kind=kind,
                target_id=target_id,
                foreground_wait_seconds=foreground_wait_seconds,
                timeout_seconds=timeout_seconds,
                on_success=on_success,
                on_timeout=on_timeout,
                cleanup_path=cleanup_path,
            )
            events = self._context.Queue()
            process = self._context.Process(
                target=_worker_entry,
                args=(worker, args, events),
                name=f"makeup-{resolved_id}",
                daemon=True,
            )
            state.events = events
            state.process = process
            self._tasks[resolved_id] = state
            self._persist(state)
            self._dispatch()
            return state.snapshot()

    def _persist(self, state):
        if self.storage and self._tasks.get(state.id) is state:
            self.storage.put('task', state.id, state.snapshot().model_dump(mode='json'))

    def _dispatch(self):
        if self._shutting_down:
            return
        for kind, limit in [('product_enrichment', 3), ('product_color', 1), ('tutorial', 1),
                            ('tutorial_analysis', 1), ('tutorial_steps', 1), ('tutorial_products', 1),
                            ('profile_analysis', 1), ('base_image', 1), ('step_image', 1)]:
            running = sum((s.kind in {'base_image', 'step_image'} if kind in {'base_image', 'step_image'} else s.kind == kind)
                          and s.status == 'running' for s in self._tasks.values())
            for state in self._tasks.values():
                if running >= limit:
                    break
                if state.kind != kind or state.status != 'queued':
                    continue
                state.process.start()
                state.status = 'running'
                state.stage = 'starting'
                state.message = '正在启动分析服务'
                state.started_at = utc_now()
                state.started_monotonic = time.monotonic()
                self._persist(state)
                running += 1
                threading.Thread(target=self._monitor, args=(state.id,), daemon=True).start()

    def _monitor(self, task_id: str) -> None:
        with self._lock:
            state = self._tasks[task_id]
        process = state.process
        events = state.events
        assert process is not None
        terminal_event: dict[str, Any] | None = None
        while True:
            with self._lock:
                if state.status == "cancelled":
                    break
                elapsed = (
                    time.monotonic() - state.started_monotonic
                    if state.started_monotonic is not None
                    else 0
                )
                if elapsed >= state.timeout_seconds:
                    state.status = "timed_out"
                    state.stage = "timed_out"
                    state.message = f"任务达到 {state.timeout_seconds:g} 秒上限，已停止"
                    state.error = state.message
                    if state.on_timeout:
                        try:
                            state.result = state.on_timeout(state.checkpoint)
                        except Exception as exc:
                            state.error += f'；部分结果整理失败：{exc}'
                    state.finished_at = utc_now()
                    state.updated_at = state.finished_at
                    break
            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                if not process.is_alive():
                    try:
                        event = events.get(timeout=0.2)
                    except queue.Empty:
                        with self._lock:
                            if state.status not in TERMINAL_STATUSES:
                                state.status = "failed"
                                state.stage = "failed"
                                state.message = "工作进程提前结束"
                                state.error = state.message
                                state.finished_at = utc_now()
                                state.updated_at = state.finished_at
                        break
                else:
                    continue
            if event.get("type") == "progress":
                with self._lock:
                    if state.status == "running":
                        state.progress = int(event["progress"])
                        state.stage = str(event["stage"])
                        state.message = str(event["message"])
                        state.updated_at = utc_now()
                continue
            if event.get('type') == 'trace':
                with self._lock:
                    state.trace.append(event['data'])
                    self._persist(state)
                continue
            if event.get('type') == 'checkpoint':
                with self._lock:
                    state.checkpoint = event['data']
                    self._persist(state)
                continue
            terminal_event = event
            break

        with self._lock:
            should_terminate = state.status in {"cancelled", "timed_out"}
        if should_terminate:
            self._stop_process(process)
        else:
            process.join(timeout=1)
            if process.is_alive():
                self._stop_process(process)

        if terminal_event is not None:
            if terminal_event.get("type") == "result":
                try:
                    result = terminal_event["result"]
                    with self._lock:
                        if state.status not in {"cancelled", "timed_out"}:
                            if result.get('_waiting_user'):
                                state.status = 'waiting_user'
                                state.stage = 'waiting_user'
                                state.message = result['_waiting_user']['question']
                                state.clarification = result['_waiting_user']
                                state.updated_at = utc_now()
                                self._persist(state)
                                self._dispatch()
                                events.close()
                                events.join_thread()
                                return
                            if state.on_success:
                                result = state.on_success(result)
                            state.status = "succeeded"
                            state.progress = 100
                            state.stage = "completed"
                            state.message = "任务已完成"
                            state.result = result
                            state.finished_at = utc_now()
                            state.updated_at = state.finished_at
                except Exception as exc:
                    with self._lock:
                        if state.status not in {"cancelled", "timed_out"}:
                            state.status = "failed"
                            state.stage = "failed"
                            state.message = "保存任务结果失败"
                            state.error = str(exc)
                            state.finished_at = utc_now()
                            state.updated_at = state.finished_at
            elif terminal_event.get("type") == "error":
                with self._lock:
                    if state.status not in {"cancelled", "timed_out"}:
                        state.status = "failed"
                        state.stage = "failed"
                        state.message = "任务执行失败"
                        state.error = str(terminal_event.get("error") or "未知错误")
                        state.finished_at = utc_now()
                        state.updated_at = state.finished_at

        with self._lock:
            self._persist(state)
            self._dispatch()

        try:
            events.close()
            events.join_thread()
        except (AttributeError, OSError, ValueError):
            pass
        if state.cleanup_path and state.cleanup_path.exists():
            shutil.rmtree(state.cleanup_path, ignore_errors=True)

    @staticmethod
    def _stop_process(process: multiprocessing.Process) -> None:
        if process.pid is None:
            return
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(timeout=1)

    def get(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            if task_id not in self._tasks:
                if task_id in self._history:
                    return self._history[task_id]
                raise KeyError(f"task not found: {task_id}")
            snapshot = self._tasks[task_id].snapshot()
            if snapshot.status == 'queued':
                waiting = [s.id for s in self._tasks.values() if s.kind == snapshot.kind and s.status == 'queued']
                snapshot.queue_position = waiting.index(task_id) + 1
            return snapshot

    def list(self) -> list[TaskSnapshot]:
        with self._lock:
            states = sorted(
                self._tasks.values(), key=lambda item: item.created_at, reverse=True
            )
            active = [self.get(state.id) for state in states]
            return sorted(active + list(self._history.values()), key=lambda snapshot: snapshot.created_at, reverse=True)

    def resume(self, task_id, *, worker, args, on_success):
        with self._lock:
            previous = self.get(task_id)
            if previous.status != 'waiting_user':
                raise ValueError('任务没有等待补充信息')
            old_state = self._tasks.pop(task_id, None)
            old_history = self._history.pop(task_id, None)
            try:
                snapshot = self.submit(kind=previous.kind, worker=worker, args=args,
                    timeout_seconds=previous.timeout_seconds, target_id=previous.target_id,
                    on_success=on_success, task_id=task_id)
                state = self._tasks[task_id]
                state.trace = previous.trace
                state.created_at = previous.created_at
                self._persist(state)
                return self.get(task_id)
            except Exception:
                if old_state:
                    self._tasks[task_id] = old_state
                if old_history:
                    self._history[task_id] = old_history
                raise

    def cancel(self, task_id: str) -> TaskSnapshot:
        with self._lock:
            if task_id not in self._tasks:
                if task_id in self._history:
                    previous = self._history[task_id]
                    if previous.status == 'waiting_user':
                        previous = previous.model_copy(update={'status': 'cancelled', 'cancellable': False, 'clarification': None, 'message': '任务已由用户取消'})
                        self._history[task_id] = previous
                        if self.storage:
                            self.storage.put('task', task_id, previous.model_dump(mode='json'))
                    return previous
                raise KeyError(f"task not found: {task_id}")
            state = self._tasks[task_id]
            if state.status in TERMINAL_STATUSES:
                return state.snapshot()
            state.status = "cancelled"
            state.stage = "cancelled"
            state.message = "任务已由用户取消"
            state.error = None
            state.finished_at = utc_now()
            state.updated_at = state.finished_at
            process = state.process
            self._persist(state)
        if process is not None:
            self._stop_process(process)
        with self._lock:
            self._dispatch()
        return self.get(task_id)

    def shutdown(self) -> None:
        with self._lock:
            self._shutting_down = True
            task_ids = [
                state.id
                for state in self._tasks.values()
                if state.status not in TERMINAL_STATUSES and state.status != 'waiting_user'
            ]
        for task_id in task_ids:
            self.cancel(task_id)
