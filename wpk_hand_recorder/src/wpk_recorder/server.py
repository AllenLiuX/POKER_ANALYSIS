from __future__ import annotations

import asyncio
import csv
import io
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .analytics import connect_readonly, hand_detail, opponent_stats, snapshot
from .inference import (
    contextual_tendencies,
    inference_backtest,
    player_profile_evidence,
    preflop_range_profile,
)
from .reasoning import (
    LLMReasoner,
    ReasoningError,
    live_decision,
    live_decision_from_hand,
    local_exploit_fallback,
    local_fast_analysis,
    public_decision,
    reasoning_context,
    simplified_range_strategy,
)
from .strategy import evaluate_money_strategy
from .storage import save_strategy_evaluation
from .squid_value import calibrate_squid_rules


class ReasoningRequest(BaseModel):
    sequence: int = Field(..., ge=1)
    force: bool = False
    analysis_mode: str = Field(
        default="auto",
        pattern="^(auto|local|llm)$",
    )
    reasoning_depth: str = Field(
        default="light",
        pattern="^(light|deep)$",
    )


class ProfileReasoningRequest(BaseModel):
    position: str = Field(default="ALL", max_length=16)
    line: str = Field(default="vpip", max_length=24)
    mode: Optional[str] = Field(default=None, pattern="^(holdem|squid)$")
    force: bool = False


def create_app(
    data_dir: Path,
    llm_reasoner: Optional[LLMReasoner] = None,
    live_hand_provider: Optional[Callable[[], Any]] = None,
) -> FastAPI:
    data_dir = data_dir.resolve()
    web_dir = Path(__file__).parent / "web"
    app = FastAPI(title="WPK 实时牌谱", docs_url=None, redoc_url=None)
    app.state.data_dir = data_dir
    app.state.llm_reasoner = llm_reasoner or LLMReasoner.from_env()
    app.state.reasoning_cache = {}
    app.state.strategy_cache = {}
    app.state.profile_reasoning_cache = {}
    app.state.live_hand_provider = live_hand_provider
    app.mount("/assets", StaticFiles(directory=web_dir), name="assets")

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
            evaluate_money_strategy, context, baseline
        )
        audit_saved = await run_in_threadpool(
            save_strategy_evaluation, data_dir, decision, evaluation
        )
        app.state.strategy_cache[cache_key] = (evaluation, audit_saved)
        while len(app.state.strategy_cache) > 64:
            app.state.strategy_cache.pop(next(iter(app.state.strategy_cache)))
        return evaluation, audit_saved

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/api/snapshot")
    async def api_snapshot(
        mode: Optional[str] = Query(default=None, pattern="^(holdem|squid)$")
    ) -> dict:
        try:
            result = snapshot(data_dir, mode=mode)
            live_hand = _live_hand_snapshot(app)
            current = public_decision(live_decision_from_hand(live_hand))
            if current and (mode is None or current.get("game_mode") == mode):
                result["live_decision"] = current
            return result
        except sqlite3.Error as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/hands/{hand_id}")
    async def api_hand(hand_id: str) -> dict:
        hand = hand_detail(data_dir, hand_id)
        if hand is None:
            raise HTTPException(status_code=404, detail="hand not found")
        return hand

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

    @app.post("/api/players/{user_id}/profile/analyze")
    async def analyze_player_profile(
        user_id: str, request: ProfileReasoningRequest
    ) -> dict:
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
                connection, user_id, mode=request.mode, limit=10
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
        cache_key = (
            user_id,
            request.mode,
            range_profile["position"],
            range_profile["line"],
            profile["hands"],
            range_profile["evidence"]["player_revealed_samples"],
        )
        if not request.force and cache_key in app.state.profile_reasoning_cache:
            return app.state.profile_reasoning_cache[cache_key]
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
                "population_deviations": context["population_deviations"],
            },
        }
        app.state.profile_reasoning_cache[cache_key] = response
        while len(app.state.profile_reasoning_cache) > 64:
            app.state.profile_reasoning_cache.pop(
                next(iter(app.state.profile_reasoning_cache))
            )
        return response

    @app.get("/api/inference/backtest")
    async def inference_metrics() -> dict:
        connection = connect_readonly(data_dir)
        try:
            return inference_backtest(connection)
        finally:
            connection.close()

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
        }

    @app.get("/api/strategy/current")
    async def current_strategy(
        sequence: Optional[int] = Query(default=None, ge=1)
    ) -> dict:
        live_hand = _live_hand_snapshot(app)
        context = await run_in_threadpool(
            _load_reasoning_context, data_dir, sequence, live_hand
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
        stale = not await run_in_threadpool(
            _decision_is_current,
            data_dir,
            int(context["decision"]["sequence"]),
            str(context["decision"].get("state_hash") or ""),
            _live_hand_snapshot(app),
        )
        return {
            "decision": context["decision"],
            "baseline": baseline,
            "money_strategy": money_strategy,
            "audit_saved": audit_saved,
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
        live_hand = _live_hand_snapshot(app)
        context = await run_in_threadpool(
            _load_reasoning_context, data_dir, request.sequence, live_hand
        )
        if context is None:
            raise HTTPException(
                status_code=409, detail="该行动点已过期或当前牌局状态不完整"
            )
        decision = context["decision"]
        strategy = simplified_range_strategy(context)
        money_strategy, _ = await evaluate_and_audit_strategy(context, strategy)
        has_hole_cards = len(decision.get("hero_cards") or []) == 2
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
                "stale": False,
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
                },
            }
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
                app.state.reasoning_cache[cache_key] = response
                return response
        if not wants_llm and not decision.get("auto_reasoning"):
            return {
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
        try:
            analyze = (
                reasoner.analyze
                if has_hole_cards
                else reasoner.analyze_exploit
            )
            call = (
                run_in_threadpool(
                    analyze,
                    context,
                    timeout,
                    request.reasoning_depth,
                )
                if request.reasoning_depth == "deep"
                else run_in_threadpool(analyze, context, timeout)
            )
            result = await asyncio.wait_for(call, timeout=timeout + 0.25)
        except asyncio.TimeoutError:
            llm_fallback = True
            fallback_reason = "GPT‑5.6 实时推理超过硬超时"
            result = local_exploit_fallback(
                context,
                fallback_reason,
                round((time.perf_counter() - llm_started) * 1000),
                request.reasoning_depth,
            )
        except ReasoningError as error:
            llm_fallback = True
            fallback_reason = str(error)
            result = local_exploit_fallback(
                context,
                fallback_reason,
                round((time.perf_counter() - llm_started) * 1000),
                request.reasoning_depth,
            )

        memory_current = live_decision_from_hand(_live_hand_snapshot(app))
        if memory_current is not None:
            current = public_decision(memory_current)
        else:
            connection = connect_readonly(data_dir)
            try:
                current = public_decision(live_decision(connection))
            finally:
                connection.close()
        stale = current is None or current.get("sequence") != request.sequence
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
                "reason": (
                    f"GPT‑5.6 未完成：{fallback_reason}；已使用本地证据回退"
                    if llm_fallback
                    else "复核当前手牌并结合活跃对手画像"
                    if has_hole_cards
                    else "观察模式：只做范围层面的对手剥削复核"
                ),
            },
        }
        if not stale:
            app.state.reasoning_cache[cache_key] = response
            while len(app.state.reasoning_cache) > 64:
                app.state.reasoning_cache.pop(next(iter(app.state.reasoning_cache)))
        return response

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
            _event_stream(request, data_dir),
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


def _load_reasoning_context(
    data_dir: Path,
    sequence: Optional[int],
    live_hand: Optional[Any] = None,
) -> Optional[dict]:
    connection = connect_readonly(data_dir)
    try:
        memory_decision = live_decision_from_hand(live_hand)
        if memory_decision is not None and (
            sequence is None
            or int(memory_decision.get("sequence") or 0) == sequence
        ):
            return reasoning_context(
                connection,
                int(memory_decision["sequence"]),
                decision=memory_decision,
            )
        if sequence is None:
            current = live_decision(connection)
            if current is None:
                return None
            sequence = int(current["sequence"])
        return reasoning_context(connection, sequence)
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


async def _event_stream(request: Request, data_dir: Path) -> AsyncIterator[str]:
    last_sequence = -1
    mode = request.query_params.get("mode")
    if mode not in {"holdem", "squid"}:
        mode = None
    while not await request.is_disconnected():
        try:
            data = snapshot(data_dir, mode=mode)
            current = int(data.get("last_sequence", 0))
            if current != last_sequence:
                last_sequence = current
                yield f"event: snapshot\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            else:
                yield ": keepalive\n\n"
        except sqlite3.Error as error:
            yield f"event: error\ndata: {json.dumps({'detail': str(error)})}\n\n"
        await asyncio.sleep(0.75)
