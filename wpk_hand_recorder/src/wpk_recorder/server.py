from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import sqlite3
import time
from copy import deepcopy
from pathlib import Path
from threading import Event
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .assistance_policy import AssistancePolicy
from .analytics import (
    connect_readonly,
    hand_detail,
    hand_summaries,
    live_snapshot,
    opponent_stats,
    snapshot,
)
from .equity_curve import EquityCurveCancelled, build_range_equity_curve
from .features import derive_positions, project_hand_state
from .inference import (
    contextual_tendencies,
    inference_backtest,
    player_profile_evidence,
    preflop_range_profile,
)
from .inference_engine import (
    InferenceEngine,
    freeze_inference_context,
    inference_context_hash,
    inference_subject,
)
from .inference_templates import template_ids
from .reasoning import (
    LLMReasoner,
    OPPONENT_NODE_MODEL_VERSION,
    ReasoningError,
    apply_exploit_frequency_shifts,
    fast_preflop_context,
    live_decision,
    live_decision_from_hand,
    local_exploit_fallback,
    local_fast_analysis,
    hero_preflop_range,
    preflop_preview_context_from_hand,
    public_decision,
    reasoning_context,
    opponent_node_profile,
    simplified_range_strategy,
)
from .strategy import evaluate_money_strategy
from .storage import (
    inference_run_rows,
    load_inference_context,
    load_profile_analysis,
    save_inference_context,
    save_inference_run,
    save_profile_analysis,
    save_strategy_evaluation,
    hand_from_dict,
)
from .squid_value import calibrate_squid_rules


class ReasoningRequest(BaseModel):
    sequence: int = Field(..., ge=1)
    state_hash: Optional[str] = Field(
        default=None,
        min_length=16,
        max_length=128,
    )
    hand_id: Optional[str] = Field(default=None, max_length=160)
    force: bool = False
    analysis_mode: str = Field(
        default="auto",
        pattern="^(auto|local|llm)$",
    )
    reasoning_depth: str = Field(
        default="light",
        pattern="^(light|deep)$",
    )


class ReasoningCancelRequest(BaseModel):
    sequence: int = Field(..., ge=1)
    state_hash: str = Field(..., min_length=1, max_length=128)


class ProfileReasoningRequest(BaseModel):
    position: str = Field(default="ALL", max_length=16)
    line: str = Field(default="vpip", max_length=24)
    mode: Optional[str] = Field(default=None, pattern="^(holdem|squid)$")
    force: bool = False


def create_app(
    data_dir: Path,
    llm_reasoner: Optional[LLMReasoner] = None,
    live_hand_provider: Optional[Callable[[], Any]] = None,
    assistance_mode: Optional[str] = None,
) -> FastAPI:
    data_dir = data_dir.resolve()
    web_dir = Path(__file__).parent / "web"
    app = FastAPI(title="WPK 实时牌谱", docs_url=None, redoc_url=None)
    app.state.data_dir = data_dir
    app.state.llm_reasoner = llm_reasoner or LLMReasoner.from_env()
    app.state.inference_engine = InferenceEngine(app.state.llm_reasoner)
    app.state.reasoning_cache = {}
    app.state.auto_reasoning_task = None
    app.state.strategy_cache = {}
    app.state.equity_curve_cache = {}
    app.state.equity_curve_tasks = {}
    app.state.profile_reasoning_cache = {}
    app.state.opponent_range_cache = {}
    app.state.snapshot_cache = {}
    app.state.snapshot_locks = {}
    app.state.live_snapshot_cache = {}
    app.state.live_snapshot_locks = {}
    app.state.inference_backtest_cache = None
    app.state.inference_backtest_lock = None
    app.state.live_hand_provider = live_hand_provider
    app.state.assistance_policy = AssistancePolicy.from_env(
        assistance_mode
    )
    app.mount("/assets", StaticFiles(directory=web_dir), name="assets")

    def require_capability(capability: str) -> None:
        policy = app.state.assistance_policy
        if policy.allows(capability):
            return
        status_code, detail = policy.denial(capability)
        raise HTTPException(status_code=status_code, detail=detail)

    async def evaluate_and_audit_strategy(
        context: dict, baseline: dict
    ) -> tuple:
        decision = context["decision"]
        cache_key = (
            int(decision.get("sequence") or 0),
            str(decision.get("state_hash") or ""),
        )
        cached = app.state.strategy_cache.get(cache_key)
        if cached is not None:
            return cached
        evaluation = await run_in_threadpool(
            evaluate_money_strategy,
            context,
            baseline,
            trials_cap=48,
        )
        audit_saved = await run_in_threadpool(
            save_strategy_evaluation, data_dir, decision, evaluation
        )
        app.state.strategy_cache[cache_key] = (evaluation, audit_saved)
        while len(app.state.strategy_cache) > 64:
            app.state.strategy_cache.pop(next(iter(app.state.strategy_cache)))
        return evaluation, audit_saved

    async def finalize_inference_response(
        response: dict,
        context: dict,
        request: ReasoningRequest,
    ) -> dict:
        subject = inference_subject(context)
        analysis = (
            response.get("analysis")
            if subject == "exact_hand"
            else response.get("exploit_analysis")
        )
        fallback = response.get("exploit_analysis")
        if subject == "exact_hand" and analysis and fallback:
            analysis = {
                **analysis,
                "opponent_reads": fallback.get("opponent_reads") or [],
                "range_adjustments": fallback.get("range_adjustments") or [],
                "frequency_shifts": fallback.get("frequency_shifts") or [],
                "evidence_details": fallback.get("evidence_details") or [],
                "caveats": fallback.get("caveats") or [],
            }
        route = dict(response.get("route") or {})
        if analysis:
            route.update(
                {
                    key: analysis.get(key)
                    for key in (
                        "template_id",
                        "template_hash",
                        "prompt_hash",
                    )
                    if analysis.get(key)
                }
            )
            route["latency_ms"] = analysis.get("latency_ms")
        advice = app.state.inference_engine.build_advice(
            context,
            baseline=response.get("strategy"),
            money_strategy=response.get("money_strategy"),
            analysis=analysis,
            route=route,
            temporal_quality=(
                "frozen_point_in_time_replay"
                if (context.get("context_integrity") or {}).get(
                    "frozen_replay"
                )
                else "point_in_time"
            ),
        )
        response["inference"] = advice
        response["exploit_strategy"] = (
            advice.get("range_policy") or {}
        ).get("adjusted")
        snapshot = freeze_inference_context(
            context,
            temporal_quality=(
                "frozen_point_in_time_replay"
                if (context.get("context_integrity") or {}).get(
                    "frozen_replay"
                )
                else "point_in_time"
            ),
        )
        context_hash = inference_context_hash(snapshot)
        await run_in_threadpool(
            save_inference_context,
            data_dir,
            context_hash,
            snapshot,
        )
        validation_status = (
            "fallback"
            if route.get("llm_called")
            and route.get("llm_completed") is False
            else "valid"
        )
        await run_in_threadpool(
            save_inference_run,
            data_dir,
            context["decision"],
            advice,
            analysis_mode=request.analysis_mode,
            reasoning_depth=request.reasoning_depth,
            status="skipped" if response.get("skipped") else "completed",
            validation_status=validation_status,
            error_reason=(
                route.get("reason") if validation_status == "fallback" else None
            ),
        )
        return response

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/api/snapshot")
    async def api_snapshot(
        mode: Optional[str] = Query(default=None, pattern="^(holdem|squid)$")
    ) -> dict:
        try:
            result = dict(await _cached_snapshot(app, data_dir, mode))
            live_hand = _live_hand_snapshot(app)
            if app.state.assistance_policy.allows("live_advice"):
                current = public_decision(
                    live_decision_from_hand(live_hand)
                )
                if current and (
                    mode is None or current.get("game_mode") == mode
                ):
                    result["live_decision"] = current
            else:
                result["live_decision"] = None
            result["assistance_policy"] = (
                app.state.assistance_policy.as_dict()
            )
            return result
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/hands/{hand_id}")
    async def api_hand(hand_id: str) -> dict:
        hand = hand_detail(data_dir, hand_id)
        if hand is None:
            raise HTTPException(status_code=404, detail="hand not found")
        return hand

    @app.get("/api/hands")
    async def api_hands(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
        mode: Optional[str] = Query(
            default=None,
            pattern="^(holdem|squid)$",
        ),
        valid_only: bool = False,
        user_id: Optional[str] = Query(default=None, max_length=128),
        revealed_only: bool = False,
        q: Optional[str] = Query(default=None, max_length=128),
        result: Optional[str] = Query(
            default=None,
            pattern="^(won|lost|even)$",
        ),
        street: Optional[str] = Query(
            default=None,
            pattern="^(preflop|flop|turn|river)$",
        ),
    ) -> dict:
        return await run_in_threadpool(
            hand_summaries,
            data_dir,
            limit=limit,
            offset=offset,
            mode=mode,
            valid_only=valid_only,
            user_id=user_id,
            revealed_only=revealed_only,
            query=q,
            result=result,
            street=street,
        )

    @app.get("/api/players/{user_id}/context")
    async def player_context(user_id: str) -> dict:
        connection = connect_readonly(data_dir)
        try:
            return contextual_tendencies(connection, user_id)
        finally:
            connection.close()

    @app.get("/api/players/{user_id}/preflop-range")
    async def player_preflop_range(
        user_id: str,
        position: str = Query(default="ALL", max_length=16),
        line: str = Query(
            default="vpip",
            pattern="^(vpip|open_raise|three_bet|cold_call|limp)$",
        ),
        mode: Optional[str] = Query(default=None, pattern="^(holdem|squid)$"),
    ) -> dict:
        require_capability("history_range")
        connection = connect_readonly(data_dir)
        try:
            return preflop_range_profile(
                connection, user_id, position=position, line=line, mode=mode
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        finally:
            connection.close()

    @app.get("/api/players/{user_id}/range-at-node")
    async def player_range_at_node(
        user_id: str,
        hand_id: Optional[str] = Query(
            default=None,
            max_length=160,
        ),
        sequence: Optional[int] = Query(default=None, ge=1),
        state_hash: Optional[str] = Query(
            default=None,
            min_length=16,
            max_length=128,
        ),
        phase: str = Query(
            default="full",
            pattern="^(preview|full)$",
        ),
    ) -> dict:
        live_hand = _live_hand_snapshot(app)
        loaded = await run_in_threadpool(
            _load_opponent_node_decision,
            data_dir,
            user_id,
            hand_id,
            sequence,
            live_hand,
        )
        if loaded is None:
            raise HTTPException(
                status_code=404,
                detail="未找到该玩家或牌局节点",
            )
        decision, node_scope, hand_status = loaded
        require_capability(
            "live_range"
            if node_scope == "live"
            else "history_range"
        )
        if (
            state_hash
            and str(decision.get("state_hash") or "") != state_hash
        ):
            raise HTTPException(
                status_code=409,
                detail="所选牌局节点已变化，请重新打开玩家范围",
            )
        cache_key = (
            str(decision.get("hand_id") or ""),
            str(decision.get("state_hash") or ""),
            str(user_id),
            OPPONENT_NODE_MODEL_VERSION,
            app.state.assistance_policy.mode,
            phase,
        )
        cached = app.state.opponent_range_cache.get(cache_key)
        if cached is None:
            try:
                profile = await run_in_threadpool(
                    _compute_opponent_node_profile,
                    data_dir,
                    decision,
                    user_id,
                    phase == "preview",
                )
            except LookupError as error:
                raise HTTPException(
                    status_code=404,
                    detail=str(error),
                ) from error
            except ValueError as error:
                raise HTTPException(
                    status_code=422,
                    detail=str(error),
                ) from error
            cached = {
                "node": {
                    "hand_id": decision.get("hand_id"),
                    "sequence": int(
                        decision.get("sequence") or 0
                    ),
                    "state_hash": decision.get("state_hash"),
                    "scope": node_scope,
                    "hand_status": hand_status,
                    "street": decision.get("street"),
                    "board": list(decision.get("board") or []),
                    "game_mode": decision.get("game_mode"),
                    "stale": False,
                },
                **profile,
                "progressive": {
                    "phase": phase,
                    "complete": phase == "full",
                },
                "policy": app.state.assistance_policy.as_dict(),
            }
            app.state.opponent_range_cache[cache_key] = cached
            while len(app.state.opponent_range_cache) > 64:
                app.state.opponent_range_cache.pop(
                    next(iter(app.state.opponent_range_cache))
                )
        response = deepcopy(cached)
        if node_scope == "live":
            current = await run_in_threadpool(
                _load_opponent_node_decision,
                data_dir,
                user_id,
                hand_id,
                sequence,
                _live_hand_snapshot(app),
            )
            response["node"]["stale"] = bool(
                current is None
                or str(current[0].get("state_hash") or "")
                != str(decision.get("state_hash") or "")
            )
        return response

    @app.post("/api/players/{user_id}/profile/analyze")
    async def analyze_player_profile(
        user_id: str, request: ProfileReasoningRequest
    ) -> dict:
        require_capability("profile_analysis")
        connection = connect_readonly(data_dir)
        try:
            profiles = opponent_stats(connection, mode=request.mode)
            profile = next(
                (item for item in profiles if item["user_id"] == user_id), None
            )
            if profile is None:
                raise HTTPException(status_code=404, detail="player not found")
            range_profile = preflop_range_profile(
                connection,
                user_id,
                position=request.position,
                line=request.line,
                mode=request.mode,
            )
            contextual = contextual_tendencies(connection, user_id)
            evidence = player_profile_evidence(
                connection, user_id, mode=request.mode, limit=6
            )
        finally:
            connection.close()
        confidence_ceiling = _profile_confidence_ceiling(
            int(profile["hands"]),
            int(range_profile["evidence"]["player_revealed_samples"]),
        )
        context = _profile_reasoning_context(
            profile, range_profile, contextual, evidence, confidence_ceiling
        )
        context_hash = _profile_context_hash(context)
        cache_key = (
            user_id,
            request.mode or "all",
            range_profile["position"],
            range_profile["line"],
            context_hash,
        )
        if not request.force and cache_key in app.state.profile_reasoning_cache:
            return app.state.profile_reasoning_cache[cache_key]
        if not request.force:
            persisted = await run_in_threadpool(
                load_profile_analysis,
                data_dir,
                user_id=user_id,
                mode=request.mode,
                position=range_profile["position"],
                line=range_profile["line"],
                context_hash=context_hash,
            )
            if persisted is not None:
                app.state.profile_reasoning_cache[cache_key] = persisted
                return persisted
        reasoner = app.state.llm_reasoner
        if not reasoner.configured:
            raise HTTPException(
                status_code=503,
                detail="LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY",
            )
        profile_timeout = float(
            getattr(
                reasoner,
                "profile_timeout_seconds",
                reasoner.timeout_seconds,
            )
        )
        try:
            analysis = await asyncio.wait_for(
                run_in_threadpool(
                    reasoner.analyze_profile, context, profile_timeout
                ),
                timeout=profile_timeout + 0.25,
            )
        except asyncio.TimeoutError as error:
            raise HTTPException(status_code=504, detail="LLM 画像复核超时") from error
        except ReasoningError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
        response = {
            "analysis": analysis,
            "profile": {
                "user_id": profile["user_id"],
                "alias": profile["alias"],
                "hands": profile["hands"],
                "style": profile.get("style"),
            },
            "range": {
                "position": range_profile["position"],
                "line": range_profile["line"],
                "estimated_range_pct": range_profile["estimated_range_pct"],
                "confidence": range_profile["confidence"],
            },
            "evidence": {
                "available_cases": evidence["available_cases"],
                "selected_cases": evidence["selected_cases"],
                "cases": evidence["cases"],
                "postflop_patterns": evidence.get("postflop_patterns") or [],
                "revealed_pattern_summary": (
                    evidence.get("revealed_pattern_summary") or []
                ),
                "population_deviations": context["population_deviations"],
            },
        }
        saved_at = await run_in_threadpool(
            save_profile_analysis,
            data_dir,
            user_id=user_id,
            mode=request.mode,
            position=range_profile["position"],
            line=range_profile["line"],
            context_hash=context_hash,
            response=response,
        )
        response["persistence"] = {
            "saved": saved_at is not None,
            "restored": False,
            "stale": False,
            "context_hash": context_hash,
            "created_at": saved_at,
        }
        app.state.profile_reasoning_cache[cache_key] = response
        while len(app.state.profile_reasoning_cache) > 64:
            app.state.profile_reasoning_cache.pop(
                next(iter(app.state.profile_reasoning_cache))
            )
        return response

    @app.get("/api/players/{user_id}/profile/analysis")
    async def saved_player_profile_analysis(
        user_id: str,
        position: str = Query(default="ALL", max_length=16),
        line: str = Query(default="vpip", max_length=24),
        mode: Optional[str] = Query(
            default=None,
            pattern="^(holdem|squid)$",
        ),
    ) -> dict:
        require_capability("profile_analysis")
        response = await run_in_threadpool(
            load_profile_analysis,
            data_dir,
            user_id=user_id,
            mode=mode,
            position=position,
            line=line,
        )
        if response is None:
            raise HTTPException(status_code=404, detail="没有已保存的 LLM 画像")
        connection = connect_readonly(data_dir)
        try:
            current = next(
                (
                    item
                    for item in opponent_stats(connection, mode=mode)
                    if item["user_id"] == user_id
                ),
                None,
            )
        finally:
            connection.close()
        saved_hands = int(
            ((response.get("profile") or {}).get("hands") or 0)
        )
        current_hands = int((current or {}).get("hands") or 0)
        response["persistence"]["stale"] = (
            current is None or current_hands != saved_hands
        )
        return response

    @app.get("/api/inference/backtest")
    async def inference_metrics() -> dict:
        cached = app.state.inference_backtest_cache
        now = time.monotonic()
        if cached is not None and now - cached[0] < 300:
            return cached[1]
        lock = app.state.inference_backtest_lock
        if lock is None:
            lock = asyncio.Lock()
            app.state.inference_backtest_lock = lock
        async with lock:
            cached = app.state.inference_backtest_cache
            now = time.monotonic()
            if cached is not None and now - cached[0] < 300:
                return cached[1]
            result = await run_in_threadpool(
                _load_inference_backtest,
                data_dir,
            )
            app.state.inference_backtest_cache = (
                time.monotonic(),
                result,
            )
            return result

    @app.get("/api/squid/calibration")
    async def squid_calibration() -> dict:
        connection = connect_readonly(data_dir)
        try:
            return calibrate_squid_rules(connection)
        finally:
            connection.close()

    @app.get("/api/reasoning/status")
    async def reasoning_status() -> dict:
        return {
            **app.state.llm_reasoner.status(),
            "mode": "advisory",
            "automatic_gate": True,
            "engine_version": "unified-inference-v1",
            "templates": template_ids(),
        }

    @app.get("/api/assistance/status")
    async def assistance_status() -> dict:
        return app.state.assistance_policy.as_dict()

    @app.get("/api/strategy/current")
    async def current_strategy(
        sequence: Optional[int] = Query(default=None, ge=1)
    ) -> dict:
        require_capability("live_advice")
        live_hand = _live_hand_snapshot(app)
        context = await run_in_threadpool(
            _load_reasoning_context,
            data_dir,
            sequence,
            live_hand,
            True,
        )
        if context is None:
            raise HTTPException(
                status_code=409,
                detail="该行动点已过期或当前牌局状态不完整",
            )
        baseline = simplified_range_strategy(context)
        money_strategy, audit_saved = await evaluate_and_audit_strategy(
            context, baseline
        )
        context["local_money_baseline"] = _compact_money_strategy(
            money_strategy
        )
        stale = not await run_in_threadpool(
            _decision_is_current,
            data_dir,
            int(context["decision"]["sequence"]),
            str(context["decision"].get("state_hash") or ""),
            _live_hand_snapshot(app),
        )
        advice = app.state.inference_engine.build_advice(
            context,
            baseline=baseline,
            money_strategy=money_strategy,
            route={
                "requested": "local",
                "llm_called": False,
                "source": baseline.get("source"),
                "reason": "本地实时基线",
            },
        )
        snapshot = freeze_inference_context(context)
        await run_in_threadpool(
            save_inference_context,
            data_dir,
            inference_context_hash(snapshot),
            snapshot,
        )
        return {
            "decision": context["decision"],
            "baseline": baseline,
            "money_strategy": money_strategy,
            "audit_saved": audit_saved,
            "stale": stale,
            "inference": advice,
        }

    @app.get("/api/strategy/preflop-preview")
    async def preflop_strategy_preview() -> dict:
        require_capability("live_advice")
        live_hand = _live_hand_snapshot(app)
        if live_hand is None:
            live_hand = await run_in_threadpool(
                _load_latest_live_hand,
                data_dir,
            )
        context = preflop_preview_context_from_hand(live_hand)
        if context is None:
            raise HTTPException(
                status_code=409,
                detail="当前没有可提前展示的本人翻前范围",
            )
        return {
            "decision": context["decision"],
            "baseline": simplified_range_strategy(context),
            "preview": True,
        }

    @app.get("/api/equity/current")
    async def current_equity_curve(
        request: Request,
        sequence: Optional[int] = Query(default=None, ge=1),
        hand_id: Optional[str] = Query(default=None, max_length=160),
        subject_seat: Optional[int] = Query(default=None, ge=0),
    ) -> dict:
        require_capability("live_equity")
        live_hand = _live_hand_snapshot(app)
        snapshot_fallback = sequence is None and bool(hand_id)
        if snapshot_fallback:
            context = await run_in_threadpool(
                _load_hand_equity_context,
                data_dir,
                str(hand_id),
                live_hand,
                subject_seat,
            )
        else:
            context = await run_in_threadpool(
                _load_reasoning_context,
                data_dir,
                sequence,
                live_hand,
            )
        if context is None:
            raise HTTPException(
                status_code=409,
                detail="当前行动点或进行中牌局状态不完整",
            )
        decision = context["decision"]
        cache_key = (
            "snapshot" if snapshot_fallback else "decision",
            str(decision.get("hand_id") or ""),
            str(decision.get("state_hash") or ""),
        )
        cached = app.state.equity_curve_cache.get(cache_key)
        if cached is None:
            hero_range = hero_preflop_range(context)
            selected_seat = decision.get("subject_seat")
            if selected_seat is None:
                selected_seat = decision.get("acting_seat")
            task_key = (
                str(decision.get("hand_id") or ""),
                str(selected_seat if selected_seat is not None else "primary"),
            )
            state_hash = str(decision.get("state_hash") or "")
            previous = app.state.equity_curve_tasks.get(task_key)
            cancel_event = Event()
            if previous is not None and previous[0] != state_hash:
                previous[1].set()
            app.state.equity_curve_tasks[task_key] = (
                state_hash,
                cancel_event,
            )

            async def cancel_on_disconnect() -> None:
                while not cancel_event.is_set():
                    if await request.is_disconnected():
                        cancel_event.set()
                        return
                    await asyncio.sleep(0.05)

            disconnect_task = asyncio.create_task(cancel_on_disconnect())
            try:
                cached = await run_in_threadpool(
                    build_range_equity_curve,
                    context,
                    hero_range,
                    cancel_event.is_set,
                )
            except EquityCurveCancelled as error:
                raise HTTPException(status_code=409, detail=str(error)) from error
            finally:
                cancel_event.set()
                disconnect_task.cancel()
                try:
                    await disconnect_task
                except asyncio.CancelledError:
                    pass
                current_task = app.state.equity_curve_tasks.get(task_key)
                if current_task is not None and current_task[1] is cancel_event:
                    app.state.equity_curve_tasks.pop(task_key, None)
            app.state.equity_curve_cache[cache_key] = cached
            while len(app.state.equity_curve_cache) > 32:
                app.state.equity_curve_cache.pop(
                    next(iter(app.state.equity_curve_cache))
                )
        stale = (
            False
            if snapshot_fallback
            else not await run_in_threadpool(
                _decision_is_current,
                data_dir,
                int(decision.get("sequence") or 0),
                str(decision.get("state_hash") or ""),
                _live_hand_snapshot(app),
            )
        )
        return {
            **cached,
            "sequence": int(decision.get("sequence") or 0),
            "hand_id": str(decision.get("hand_id") or ""),
            "state_hash": str(decision.get("state_hash") or ""),
            "subject_seat": decision.get("subject_seat"),
            "context_source": (
                "hand_snapshot" if snapshot_fallback else "action_node"
            ),
            "stale": stale,
        }

    @app.get("/api/strategy/evaluations")
    async def strategy_evaluations(
        limit: int = Query(default=50, ge=1, le=500)
    ) -> dict:
        connection = connect_readonly(data_dir)
        try:
            rows = connection.execute(
                """
                SELECT decision_id, state_hash, sequence, hand_id, created_at,
                       engine_version, status, recommended_action, raise_to,
                       confidence, payload_json
                FROM strategy_evaluations
                ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return {
            "evaluations": [
                {
                    "decision_id": row["decision_id"],
                    "state_hash": row["state_hash"],
                    "sequence": row["sequence"],
                    "hand_id": row["hand_id"],
                    "created_at": row["created_at"],
                    "engine_version": row["engine_version"],
                    "status": row["status"],
                    "recommended_action": row["recommended_action"],
                    "raise_to": row["raise_to"],
                    "confidence": row["confidence"],
                    "evaluation": json.loads(row["payload_json"]),
                }
                for row in rows
            ]
        }

    @app.post("/api/reasoning/analyze")
    async def analyze_decision(request: ReasoningRequest) -> dict:
        require_capability("live_advice")
        live_hand = _live_hand_snapshot(app)
        context = await run_in_threadpool(
            _load_reasoning_context, data_dir, request.sequence, live_hand
        )
        if (
            context is not None
            and request.state_hash
            and str(
                (context.get("decision") or {}).get("state_hash") or ""
            )
            != request.state_hash
        ):
            context = None
        if context is None and request.force:
            context = await run_in_threadpool(
                load_inference_context,
                data_dir,
                request.sequence,
                state_hash=request.state_hash,
                hand_id=request.hand_id,
            )
        if context is None:
            raise HTTPException(
                status_code=409, detail="该行动点已过期或当前牌局状态不完整"
            )
        decision = context["decision"]
        frozen_replay = bool(
            (context.get("context_integrity") or {}).get("frozen_replay")
        )
        strategy = simplified_range_strategy(context)
        money_strategy, _ = await evaluate_and_audit_strategy(context, strategy)
        context["local_money_baseline"] = _compact_money_strategy(
            money_strategy
        )
        subject = inference_subject(context)
        has_hole_cards = subject == "exact_hand"
        wants_llm = request.analysis_mode == "llm"
        wants_local = request.analysis_mode == "local"
        cache_key = (
            f"{request.sequence}:{decision.get('state_hash')}:"
            f"{request.analysis_mode}:"
            f"{request.reasoning_depth}"
        )
        if wants_local or (not has_hole_cards and not wants_llm):
            local = local_fast_analysis(context) if has_hole_cards else None
            response = {
                "skipped": False,
                "stale": frozen_replay,
                "decision": decision,
                "strategy": strategy,
                "money_strategy": money_strategy,
                "analysis": local,
                "exploit_analysis": None,
                "route": {
                    "requested": request.analysis_mode,
                    "llm_called": False,
                    "source": (
                        local.get("source")
                        if local
                        else strategy.get("source")
                    ),
                    "reason": (
                        "用户选择本地分析"
                        if wants_local
                        else "没有可靠底牌，自动模式仅返回本地范围"
                    ),
                    "temporal_quality": (
                        "frozen_point_in_time_replay"
                        if frozen_replay
                        else "point_in_time"
                    ),
                },
            }
            response = await finalize_inference_response(
                response,
                context,
                request,
            )
            app.state.reasoning_cache[cache_key] = response
            while len(app.state.reasoning_cache) > 64:
                app.state.reasoning_cache.pop(next(iter(app.state.reasoning_cache)))
            return response
        if not request.force and cache_key in app.state.reasoning_cache:
            return app.state.reasoning_cache[cache_key]
        if not wants_llm:
            local = local_fast_analysis(context)
            if local is not None:
                response = {
                    "skipped": False,
                    "stale": False,
                    "decision": decision,
                    "analysis": local,
                    "exploit_analysis": None,
                    "strategy": strategy,
                    "money_strategy": money_strategy,
                    "route": {
                        "requested": "auto",
                        "llm_called": False,
                        "source": local.get("source"),
                        "reason": "命中高确定性本地快路径",
                    },
                }
                response = await finalize_inference_response(
                    response,
                    context,
                    request,
                )
                app.state.reasoning_cache[cache_key] = response
                return response
        if not wants_llm and not decision.get("auto_reasoning"):
            response = {
                "skipped": True,
                "reason": decision.get("auto_reason"),
                "decision": decision,
                "strategy": strategy,
                "money_strategy": money_strategy,
                "route": {
                    "requested": "auto",
                    "llm_called": False,
                    "source": strategy.get("source"),
                    "reason": decision.get("auto_reason"),
                },
            }
            return await finalize_inference_response(
                response,
                context,
                request,
            )
        reasoner = app.state.llm_reasoner
        if not reasoner.configured:
            raise HTTPException(
                status_code=503,
                detail="LLM 未配置：请设置 WPK_LLM_API_KEY 或 MODEL_GATEWAY_KEY",
            )
        remaining_ms = int(decision.get("remaining_ms") or 0)
        if remaining_ms and remaining_ms < 1800 and not request.force:
            raise HTTPException(
                status_code=409, detail="剩余行动时间不足，已跳过远程 LLM"
            )
        timeout = (
            getattr(
                reasoner,
                "deep_timeout_seconds",
                reasoner.timeout_seconds,
            )
            if request.reasoning_depth == "deep"
            else reasoner.timeout_seconds
        )
        if remaining_ms and not request.force:
            timeout = min(timeout, max(0.5, (remaining_ms - 1200) / 1000))
        llm_started = time.perf_counter()
        llm_fallback = False
        fallback_reason = ""
        current_task = asyncio.current_task()
        previous_auto = app.state.auto_reasoning_task
        if (
            previous_auto is not None
            and previous_auto[1] is not current_task
            and not previous_auto[1].done()
        ):
            previous_auto[1].cancel()
        auto_task_key = (
            f"{request.sequence}:{decision.get('state_hash') or ''}"
        )
        registered_auto = not request.force and current_task is not None
        if registered_auto:
            app.state.auto_reasoning_task = (auto_task_key, current_task)
        try:
            call = app.state.inference_engine.run_remote_async(
                context,
                timeout,
                request.reasoning_depth,
            )
            remote_result = await asyncio.wait_for(
                call,
                timeout=timeout + 0.25,
            )
            result = remote_result["analysis"]
        except asyncio.TimeoutError:
            llm_fallback = True
            fallback_reason = "GPT‑5.6 实时推理超过硬超时"
            result = local_exploit_fallback(
                context,
                fallback_reason,
                round((time.perf_counter() - llm_started) * 1000),
                request.reasoning_depth,
            )
        except asyncio.CancelledError as error:
            raise HTTPException(
                status_code=409,
                detail="该实时推理已取消（行动点更新或客户端断开）",
            ) from error
        except ReasoningError as error:
            llm_fallback = True
            fallback_reason = str(error)
            result = local_exploit_fallback(
                context,
                fallback_reason,
                round((time.perf_counter() - llm_started) * 1000),
                request.reasoning_depth,
            )
        finally:
            if (
                registered_auto
                and app.state.auto_reasoning_task is not None
                and app.state.auto_reasoning_task[1] is current_task
            ):
                app.state.auto_reasoning_task = None

        exploit_strategy = apply_exploit_frequency_shifts(
            strategy,
            result,
        )
        stale = not await run_in_threadpool(
            _decision_is_current,
            data_dir,
            request.sequence,
            str(decision.get("state_hash") or ""),
            _live_hand_snapshot(app),
        )
        response = {
            "skipped": False,
            "stale": stale,
            "decision": decision,
            "analysis": (
                local_fast_analysis(context)
                if has_hole_cards and llm_fallback
                else result
                if has_hole_cards
                else None
            ),
            "exploit_analysis": (
                result if not has_hole_cards or llm_fallback else None
            ),
            "strategy": strategy,
            "exploit_strategy": exploit_strategy,
            "money_strategy": money_strategy,
            "route": {
                "requested": (
                    "llm" if wants_llm else "auto"
                ),
                "llm_called": True,
                "llm_completed": not llm_fallback,
                "reasoning_depth": request.reasoning_depth,
                "timeout_seconds": timeout,
                "reasoning_effort": (
                    getattr(
                        reasoner,
                        "deep_reasoning_effort",
                        "high",
                    )
                    if request.reasoning_depth == "deep"
                    else getattr(reasoner, "reasoning_effort", "low")
                ),
                "source": result.get("source"),
                "template_id": result.get("template_id"),
                "template_hash": result.get("template_hash"),
                "prompt_hash": result.get("prompt_hash"),
                "reason": (
                    f"GPT‑5.6 未完成：{fallback_reason}；已使用本地证据回退"
                    if llm_fallback
                    else "基于已冻结行动点复盘；牌局已继续"
                    if frozen_replay
                    else "复核当前手牌并结合活跃对手画像"
                    if has_hole_cards
                    else "观察模式：只做范围层面的对手剥削复核"
                ),
                "temporal_quality": (
                    "frozen_point_in_time_replay"
                    if frozen_replay
                    else "point_in_time"
                ),
            },
        }
        response = await finalize_inference_response(
            response,
            context,
            request,
        )
        if not stale:
            app.state.reasoning_cache[cache_key] = response
            while len(app.state.reasoning_cache) > 64:
                app.state.reasoning_cache.pop(next(iter(app.state.reasoning_cache)))
        return response

    @app.post("/api/inference/cancel", status_code=204)
    async def cancel_inference(request: ReasoningCancelRequest) -> Response:
        task_key = f"{request.sequence}:{request.state_hash}"
        current = app.state.auto_reasoning_task
        if (
            current is not None
            and current[0] == task_key
            and not current[1].done()
        ):
            current[1].cancel()
        return Response(status_code=204)

    @app.post("/api/inference/analyze")
    async def analyze_inference(request: ReasoningRequest) -> dict:
        response = await analyze_decision(request)
        advice = dict(response.get("inference") or {})
        advice["stale"] = bool(response.get("stale"))
        advice["skipped"] = bool(response.get("skipped"))
        advice["reason"] = response.get("reason")
        return advice

    @app.get("/api/inference/runs")
    async def list_inference_runs(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict:
        rows = await run_in_threadpool(
            inference_run_rows,
            data_dir,
            limit=limit,
        )
        return {"runs": rows}

    @app.get("/api/export/hands.csv")
    async def export_hands() -> Response:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "hand_id",
                "hand_number",
                "played_at_utc",
                "game_mode",
                "street",
                "sequence",
                "user_id",
                "alias",
                "seat",
                "action",
                "amount",
                "amount_to",
                "stack_after",
            ]
        )
        connection = connect_readonly(data_dir)
        try:
            for row in connection.execute(
                """
                SELECT h.hand_id, h.hand_number, h.played_at, h.game_mode,
                       a.street, a.sequence,
                       a.user_id, a.alias, a.seat, a.action, a.amount,
                       a.amount_to, a.stack_after
                FROM hands h JOIN actions a ON a.hand_id = h.hand_id
                WHERE h.excluded_from_stats = 0
                ORDER BY h.played_at, h.hand_number, a.sequence
                """
            ):
                writer.writerow(tuple(row))
        finally:
            connection.close()
        return Response(
            output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="wpk-actions.csv"'},
        )

    @app.get("/api/export/hands.json")
    async def export_hands_json() -> Response:
        connection = connect_readonly(data_dir)
        try:
            hands = []
            for row in connection.execute(
                """
                SELECT hand_json, hand_number, played_at
                FROM hands ORDER BY played_at, hand_number
                """
            ):
                hand = json.loads(row["hand_json"])
                hand["hand_number"] = row["hand_number"]
                hand["played_at"] = row["played_at"]
                hands.append(hand)
        finally:
            connection.close()
        return Response(
            json.dumps(hands, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="wpk-hands.json"'},
        )

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            _event_stream(request, data_dir, app),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


def _profile_confidence_ceiling(hands: int, revealed_samples: int) -> str:
    if hands < 100 or revealed_samples < 8:
        return "low"
    if hands < 300 or revealed_samples < 20:
        return "medium"
    return "high"


def _profile_context_hash(context: dict) -> str:
    canonical = json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _profile_reasoning_context(
    profile: dict,
    range_profile: dict,
    contextual: dict,
    evidence: dict,
    confidence_ceiling: str,
) -> dict:
    metrics = {}
    for key, value in (profile.get("metrics") or {}).items():
        if int(value.get("opportunities") or 0) < 3:
            continue
        metrics[key] = {
            field: value.get(field)
            for field in (
                "mean_pct",
                "population_mean_pct",
                "low_pct",
                "high_pct",
                "successes",
                "opportunities",
                "confidence",
            )
        }
    contexts = {
        key: value
        for key, value in (contextual.get("contexts") or {}).items()
        if int(value.get("samples") or 0) >= 5
    }
    contexts = dict(
        sorted(
            contexts.items(),
            key=lambda item: -int(item[1].get("samples") or 0),
        )[:12]
    )
    position_metrics = {}
    for position, values in (profile.get("positions") or {}).items():
        position_metrics[position] = {
            metric: {
                field: value.get(field)
                for field in (
                    "mean_pct",
                    "population_mean_pct",
                    "low_pct",
                    "high_pct",
                    "opportunities",
                    "confidence",
                )
            }
            for metric, value in values.items()
            if metric in {"vpip", "rfi", "three_bet"}
            and int(value.get("opportunities") or 0) >= 3
        }
    return {
        "player": {
            "identity": "target_player",
            "hands": profile.get("hands"),
            "style": profile.get("style"),
            "metrics": metrics,
            "aggression_factor": profile.get("aggression_factor"),
            "wtsd_pct": profile.get("wtsd_pct"),
            "w_sd_pct": profile.get("w_sd_pct"),
            "local_tendencies": profile.get("tendencies") or [],
            "caveats": profile.get("profile_caveats") or [],
            "position_metrics": position_metrics,
            "bet_sizing": profile.get("sizing") or {},
        },
        "selected_preflop_range": {
            "position": range_profile.get("position"),
            "line": range_profile.get("line"),
            "estimated_range_pct": range_profile.get("estimated_range_pct"),
            "frequency": range_profile.get("frequency"),
            "confidence": range_profile.get("confidence"),
            "evidence": range_profile.get("evidence"),
            "top_hands": [
                {
                    "hand": item.get("hand"),
                    "weight_pct": item.get("weight_pct"),
                    "player_reveals": item.get("player_reveals"),
                }
                for item in (range_profile.get("top_hands") or [])[:12]
            ],
        },
        "contextual_actions": contexts,
        "showdown_summary": contextual.get("showdowns") or {},
        "population_deviations": _profile_deviations(profile),
        "evidence_cases": evidence,
        "confidence_ceiling": confidence_ceiling,
        "limitations": [
            "公开亮牌不是随机样本，偏向打到摊牌的较强或较黏范围",
            "范围数值由本地模型生成，LLM 只能解释，不能修改数值",
            "没有与当前桌型完全匹配且经验证的 GTO 解集",
            "population_deviations 是相对当前牌池，不是相对 GTO",
        ],
    }


def _profile_deviations(profile: dict) -> list:
    candidates = []

    def collect(scope: str, metric: str, value: dict) -> None:
        opportunities = int(value.get("opportunities") or 0)
        mean = value.get("mean_pct")
        baseline = value.get("population_mean_pct")
        if opportunities < 5 or mean is None or baseline is None:
            return
        delta = round(float(mean) - float(baseline), 1)
        separated = bool(
            float(baseline) < float(value.get("low_pct") or 0)
            or float(baseline) > float(value.get("high_pct") or 100)
        )
        score = abs(delta) * min(1.0, (opportunities / 30) ** 0.5)
        if separated:
            score += 12
        candidates.append(
            {
                "_score": score,
                "evidence_id": f"metric:{scope}:{metric}",
                "scope": scope,
                "metric": metric,
                "posterior_pct": mean,
                "population_pct": baseline,
                "delta_pp": delta,
                "direction": "above_pool" if delta > 0 else "below_pool",
                "interval_pct": [
                    value.get("low_pct"),
                    value.get("high_pct"),
                ],
                "opportunities": opportunities,
                "confidence": value.get("confidence"),
                "interval_excludes_pool": separated,
            }
        )

    for metric, value in (profile.get("metrics") or {}).items():
        collect("all", metric, value)
    for position, values in (profile.get("positions") or {}).items():
        for metric, value in values.items():
            if metric in {"vpip", "rfi", "three_bet"}:
                collect(f"position_{position}", metric, value)
    return [
        {key: value for key, value in item.items() if key != "_score"}
        for item in sorted(candidates, key=lambda item: -item["_score"])[:10]
    ]


def _compact_money_strategy(
    strategy: Dict[str, Any],
) -> Dict[str, Any]:
    compact = {
        key: strategy.get(key)
        for key in (
            "engine_version",
            "status",
            "recommended",
            "most_frequent",
            "policy",
            "candidates",
            "confidence",
            "fit_profile",
            "hero_bucket",
            "preferred_bet_fraction",
            "preferred_preflop_raise_to",
            "preflop_sizing_plan",
            "caveats",
            "reasons",
        )
        if strategy.get(key) is not None
    }
    selection = strategy.get("random_selection") or {}
    if selection:
        compact["random_selection"] = {
            key: selection.get(key)
            for key in ("method", "draw_pct", "selected_id")
        }
    return compact


def _compute_opponent_node_profile(
    data_dir: Path,
    decision: Dict[str, Any],
    user_id: str,
    preview: bool = False,
) -> Dict[str, Any]:
    connection = connect_readonly(data_dir)
    try:
        return opponent_node_profile(
            connection,
            decision,
            user_id,
            preview=preview,
        )
    finally:
        connection.close()


def _load_opponent_node_decision(
    data_dir: Path,
    user_id: str,
    hand_id: Optional[str] = None,
    sequence: Optional[int] = None,
    live_hand: Optional[Any] = None,
) -> Optional[tuple]:
    connection = connect_readonly(data_dir)
    try:
        hand_data = None
        live_hand_id = str(getattr(live_hand, "hand_id", "") or "")
        if (
            live_hand is not None
            and hasattr(live_hand, "as_dict")
            and (not hand_id or live_hand_id == str(hand_id))
        ):
            hand_data = live_hand.as_dict()
        if hand_data is None:
            if hand_id:
                row = connection.execute(
                    "SELECT hand_json FROM hands WHERE hand_id = ?",
                    (str(hand_id),),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT hand_json FROM hands
                    WHERE status = 'in_progress'
                    ORDER BY rowid DESC
                    LIMIT 1
                    """
                ).fetchone()
            if row is None:
                return None
            hand_data = json.loads(row["hand_json"])
        hand = hand_from_dict(hand_data)
        positions = derive_positions(hand)
        for seat, position in positions.items():
            if seat in hand.players:
                hand.players[seat].position = position
        target = next(
            (
                player
                for player in hand.players.values()
                if str(player.user_id or "") == str(user_id)
            ),
            None,
        )
        if target is None:
            return None
        ordered_actions = sorted(
            hand.actions,
            key=lambda action: int(action.sequence or 0),
        )
        max_action_sequence = max(
            (int(action.sequence or 0) for action in ordered_actions),
            default=0,
        )
        selected_sequence = (
            int(sequence)
            if sequence is not None
            else max_action_sequence
        )
        target_fold_sequence = next(
            (
                int(action.sequence or 0)
                for action in ordered_actions
                if action.seat == target.seat
                and str(action.action or "")
                .lower()
                .replace("-", "_")
                .replace(" ", "_")
                == "fold"
                and int(action.sequence or 0) <= selected_sequence
            ),
            None,
        )
        if target_fold_sequence is not None:
            selected_sequence = target_fold_sequence
        visible_actions = [
            action
            for action in ordered_actions
            if int(action.sequence or 0) <= selected_sequence
        ]
        if sequence is not None and not visible_actions:
            return None
        projected = project_hand_state(
            hand,
            before_action_sequence=(
                selected_sequence + 1
                if selected_sequence > 0
                else None
            ),
        )
        street = projected.street or "preflop"
        board_count = {
            "preflop": 0,
            "flop": 3,
            "turn": 4,
            "river": 5,
        }.get(street, len(hand.board))
        visible_board = list(hand.board[:board_count])
        if sequence is None and target_fold_sequence is None:
            visible_board = list(hand.board)
            street = {
                3: "flop",
                4: "turn",
                5: "river",
            }.get(len(visible_board), street)
        active_seats = sorted(projected.active_seats)
        hero = next(
            (
                player
                for player in hand.players.values()
                if player.is_hero
            ),
            None,
        )
        target_stack = projected.stacks.get(target.seat)
        if target_stack is None:
            target_stack = (
                target.stack_end
                if target.stack_end is not None
                else target.stack_start
            )
        big_blind = float(hand.big_blind or 0)
        node_hand = hand.as_dict()
        action_payload = [
            dict(action)
            for action in node_hand.get("actions") or []
            if int(action.get("sequence") or 0) <= selected_sequence
        ]
        node_hand["actions"] = action_payload
        node_hand["board"] = visible_board
        node_payload = {
            "hand_id": hand.hand_id,
            "sequence": selected_sequence,
            "board": visible_board,
            "actions": [
                {
                    key: action.get(key)
                    for key in (
                        "street",
                        "seat",
                        "action",
                        "amount",
                        "amount_to",
                        "sequence",
                    )
                }
                for action in action_payload
            ],
        }
        state_hash = hashlib.sha256(
            json.dumps(
                node_payload,
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        decision = {
            "sequence": selected_sequence,
            "state_hash": state_hash,
            "hand_id": hand.hand_id,
            "street": street,
            "acting_seat": target.seat,
            "subject_seat": target.seat,
            "decision_subject": "observer",
            "hero_cards": (
                list(hero.hole_cards or []) if hero else []
            ),
            "hero_position": target.position,
            "board": visible_board,
            "pot": projected.reconstructed_pot,
            "hero_stack": target_stack,
            "hero_stack_bb": (
                float(target_stack) / big_blind
                if target_stack is not None and big_blind > 0
                else None
            ),
            "players_in_hand": len(active_seats),
            "table_players": len(hand.players),
            "action_order": active_seats,
            "players": [
                {
                    "seat": seat,
                    "user_id": player.user_id,
                    "alias": player.alias,
                    "position": player.position,
                    "stack": projected.stacks.get(seat),
                    "active": seat in projected.active_seats,
                    "folded": seat in projected.folded_seats,
                    "is_hero": bool(player.is_hero),
                }
                for seat, player in sorted(hand.players.items())
            ],
            "big_blind": hand.big_blind,
            "small_blind": hand.small_blind,
            "ante": hand.ante,
            "game_mode": hand.game_mode,
            "legal_actions": [],
            "call_score": 0,
            "_hero_user_id": hero.user_id if hero else None,
            "_hand": node_hand,
            "_as_of_sequence": selected_sequence,
        }
        return (
            decision,
            (
                "live"
                if str(hand.status or "") == "in_progress"
                else "review"
            ),
            str(hand.status or ""),
        )
    finally:
        connection.close()


def _load_hand_equity_context(
    data_dir: Path,
    hand_id: str,
    live_hand: Optional[Any] = None,
    subject_seat: Optional[int] = None,
) -> Optional[dict]:
    connection = connect_readonly(data_dir)
    try:
        hand = None
        if (
            live_hand is not None
            and str(getattr(live_hand, "hand_id", "")) == hand_id
            and hasattr(live_hand, "as_dict")
        ):
            hand = live_hand.as_dict()
        if hand is None:
            row = connection.execute(
                "SELECT hand_json FROM hands WHERE hand_id = ?",
                (hand_id,),
            ).fetchone()
            if row is None:
                return None
            hand = json.loads(row["hand_json"])
        board = list(hand.get("board") or [])
        if hand.get("status") != "in_progress" or len(board) < 3:
            return None
        players = [
            player
            for player in hand.get("players") or []
            if isinstance(player, dict) and player.get("seat") is not None
        ]
        actions = [
            action
            for action in hand.get("actions") or []
            if isinstance(action, dict)
        ]
        folded = {
            int(action["seat"])
            for action in actions
            if action.get("seat") is not None
            and str(action.get("action") or "") == "fold"
        }
        players_by_seat = {
            int(player["seat"]): player for player in players
        }
        active_seats = [
            seat for seat in players_by_seat if seat not in folded
        ]
        selected_seat = (
            int(subject_seat)
            if subject_seat is not None
            and int(subject_seat) in active_seats
            else next(
                (
                    seat
                    for seat in active_seats
                    if players_by_seat[seat].get("is_hero") is True
                ),
                None,
            )
        )
        if selected_seat is None:
            selected_seat = next(
                (
                    int(action["seat"])
                    for action in reversed(actions)
                    if action.get("seat") is not None
                    and int(action["seat"]) in active_seats
                ),
                active_seats[0] if active_seats else None,
            )
        if selected_seat is None:
            return None
        actor = players_by_seat[selected_seat]
        sequence_row = connection.execute(
            """
            SELECT MAX(sequence) AS sequence
            FROM raw_events WHERE hand_id = ?
            """,
            (hand_id,),
        ).fetchone()
        sequence = int((sequence_row or {})["sequence"] or 0)
        state_hash = hashlib.sha256(
            json.dumps(
                {
                    "hand_id": hand_id,
                    "board": board,
                    "subject_seat": selected_seat,
                    "actions": [
                        {
                            key: action.get(key)
                            for key in (
                                "street",
                                "seat",
                                "action",
                                "amount",
                                "amount_to",
                                "sequence",
                            )
                        }
                        for action in actions
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        actor_stack = actor.get("stack_end")
        if actor_stack is None:
            actor_stack = actor.get("stack_start")
        big_blind = float(hand.get("big_blind") or 0)
        decision = {
            "sequence": sequence,
            "state_hash": state_hash,
            "hand_id": hand_id,
            "street": {
                3: "flop",
                4: "turn",
                5: "river",
            }.get(len(board), "preflop"),
            "acting_seat": selected_seat,
            "subject_seat": selected_seat,
            "decision_subject": (
                "self" if actor.get("is_hero") is True else "observer"
            ),
            "hero_cards": list(actor.get("hole_cards") or []),
            "hero_position": actor.get("position"),
            "board": board,
            "pot": hand.get("pot"),
            "hero_stack": actor_stack,
            "hero_stack_bb": (
                float(actor_stack) / big_blind
                if actor_stack is not None and big_blind > 0
                else None
            ),
            "players_in_hand": len(active_seats),
            "table_players": len(players),
            "action_order": sorted(active_seats),
            "players": [
                {
                    "seat": seat,
                    "position": player.get("position"),
                    "stack": (
                        player.get("stack_end")
                        if player.get("stack_end") is not None
                        else player.get("stack_start")
                    ),
                    "active": seat in active_seats,
                    "folded": seat in folded,
                    "is_hero": seat == selected_seat,
                }
                for seat, player in sorted(players_by_seat.items())
            ],
            "big_blind": hand.get("big_blind"),
            "small_blind": hand.get("small_blind"),
            "ante": hand.get("ante"),
            "game_mode": hand.get("game_mode"),
            "legal_actions": [],
            "call_score": 0,
            "_hero_user_id": actor.get("user_id"),
            "_hand": hand,
        }
        return reasoning_context(
            connection,
            sequence,
            decision=decision,
        )
    finally:
        connection.close()


def _load_reasoning_context(
    data_dir: Path,
    sequence: Optional[int],
    live_hand: Optional[Any] = None,
    fast_preflop: bool = False,
) -> Optional[dict]:
    connection = connect_readonly(data_dir)
    try:
        memory_decision = live_decision_from_hand(live_hand)
        if memory_decision is not None and (
            sequence is None
            or int(memory_decision.get("sequence") or 0) == sequence
        ):
            if fast_preflop:
                fast_context = fast_preflop_context(memory_decision)
                if fast_context is not None:
                    return fast_context
            return reasoning_context(
                connection,
                int(memory_decision["sequence"]),
                decision=memory_decision,
            )
        current = live_decision(connection, sequence=sequence)
        if current is None:
            return None
        if fast_preflop:
            fast_context = fast_preflop_context(current)
            if fast_context is not None:
                return fast_context
        return reasoning_context(
            connection,
            int(current["sequence"]),
            decision=current,
        )
    finally:
        connection.close()


def _decision_is_current(
    data_dir: Path,
    sequence: int,
    state_hash: str,
    live_hand: Optional[Any] = None,
) -> bool:
    current = public_decision(live_decision_from_hand(live_hand))
    if current is None:
        connection = connect_readonly(data_dir)
        try:
            current = public_decision(live_decision(connection))
        finally:
            connection.close()
    return bool(
        current
        and int(current.get("sequence") or 0) == sequence
        and str(current.get("state_hash") or "") == state_hash
    )


def _live_hand_snapshot(app: FastAPI) -> Optional[Any]:
    provider = getattr(app.state, "live_hand_provider", None)
    if provider is None:
        return None
    try:
        return provider()
    except (AttributeError, RuntimeError):
        return None


def _load_latest_live_hand(data_dir: Path) -> Optional[Any]:
    connection = connect_readonly(data_dir)
    try:
        row = connection.execute(
            """
            SELECT hand_json
            FROM hands
            WHERE status = 'in_progress'
            ORDER BY rowid DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            return None
        return hand_from_dict(json.loads(row["hand_json"]))
    finally:
        connection.close()


def _load_inference_backtest(data_dir: Path) -> dict:
    connection = connect_readonly(data_dir)
    try:
        return inference_backtest(connection)
    finally:
        connection.close()


async def _cached_snapshot(
    app: FastAPI,
    data_dir: Path,
    mode: Optional[str],
) -> dict:
    """Share one recent database snapshot across all dashboard clients."""

    key = mode or "all"
    cache = app.state.snapshot_cache
    cached = cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < 0.7:
        return cached[1]
    lock = app.state.snapshot_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 0.7:
            return cached[1]
        data = await run_in_threadpool(
            snapshot,
            data_dir,
            30,
            80,
            mode,
        )
        cache[key] = (time.monotonic(), data)
        return data


async def _cached_live_snapshot(
    app: FastAPI,
    data_dir: Path,
    mode: Optional[str],
) -> dict:
    """Share the lightweight table-state read across dashboard clients."""

    key = mode or "all"
    cache = app.state.live_snapshot_cache
    cached = cache.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < 0.2:
        return cached[1]
    lock = app.state.live_snapshot_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = cache.get(key)
        now = time.monotonic()
        if cached is not None and now - cached[0] < 0.2:
            return cached[1]
        data = await run_in_threadpool(
            live_snapshot,
            data_dir,
            3,
            30,
            mode,
        )
        cache[key] = (time.monotonic(), data)
        return data


async def _event_stream(
    request: Request,
    data_dir: Path,
    app: Optional[FastAPI] = None,
) -> AsyncIterator[str]:
    last_sequence = -1
    mode = request.query_params.get("mode")
    if mode not in {"holdem", "squid"}:
        mode = None
    while not await request.is_disconnected():
        try:
            data = (
                await _cached_live_snapshot(app, data_dir, mode)
                if app is not None
                else await run_in_threadpool(
                    live_snapshot,
                    data_dir,
                    3,
                    30,
                    mode,
                )
            )
            current = int(data.get("last_sequence", 0))
            if current != last_sequence:
                last_sequence = current
                payload = dict(data)
                if app is not None:
                    live_hand = _live_hand_snapshot(app)
                    if app.state.assistance_policy.allows("live_advice"):
                        current_decision = public_decision(
                            live_decision_from_hand(live_hand)
                        )
                        if current_decision and (
                            mode is None
                            or current_decision.get("game_mode") == mode
                        ):
                            payload["live_decision"] = current_decision
                    else:
                        payload["live_decision"] = None
                    payload["assistance_policy"] = (
                        app.state.assistance_policy.as_dict()
                    )
                yield (
                    "event: snapshot\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
            else:
                yield ": keepalive\n\n"
        except sqlite3.Error as error:
            yield f"event: error\ndata: {json.dumps({'detail': str(error)})}\n\n"
        await asyncio.sleep(0.25)
