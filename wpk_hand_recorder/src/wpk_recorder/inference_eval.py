from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .inference_engine import InferenceEngine
from .inference_templates import ADVICE_SCHEMA_VERSION, get_template
from .reasoning import ReasoningError, simplified_range_strategy
from .storage import (
    inference_context_rows,
    save_inference_run,
)
from .strategy import evaluate_money_strategy


def evaluate_prompt_templates(
    data_dir: Path,
    reasoner: Any,
    template_ids: Sequence[str],
    *,
    limit: int = 100,
    repeats: int = 1,
    reasoning_depth: str = "deep",
    persist: bool = True,
) -> Dict[str, Any]:
    rows = inference_context_rows(data_dir, limit=limit)
    engine = InferenceEngine(reasoner)
    reports = []
    for template_id in template_ids:
        template = get_template(template_id)
        eligible = [
            row for row in rows if row.get("subject") == template.subject
        ]
        attempts: List[Dict[str, Any]] = []
        signatures: Dict[str, set] = {}
        for row in eligible:
            payload = row.get("payload") or {}
            context = payload.get("context") or {}
            baseline = simplified_range_strategy(context)
            money = evaluate_money_strategy(context, baseline)
            for _ in range(max(1, min(int(repeats), 10))):
                attempt = _evaluate_once(
                    engine,
                    context,
                    baseline,
                    money,
                    template_id,
                    reasoning_depth,
                    row,
                    data_dir,
                    persist,
                )
                attempts.append(attempt)
                if attempt.get("signature"):
                    signatures.setdefault(
                        str(row.get("context_hash") or ""),
                        set(),
                    ).add(attempt["signature"])
        reports.append(
            _summarize_template(
                template_id,
                template.subject,
                attempts,
                signatures,
            )
        )
    return {
        "context_count": len(rows),
        "reasoning_depth": reasoning_depth,
        "repeats": max(1, min(int(repeats), 10)),
        "templates": reports,
        "accuracy_note": (
            "实际玩家动作仅计算行为一致率；策略质量优先看合法性、"
            "证据通过率、稳定性和可计算的 Money-EV regret。"
        ),
    }


def _evaluate_once(
    engine: InferenceEngine,
    context: Dict[str, Any],
    baseline: Dict[str, Any],
    money: Dict[str, Any],
    template_id: str,
    reasoning_depth: str,
    row: Dict[str, Any],
    data_dir: Path,
    persist: bool,
) -> Dict[str, Any]:
    timeout = (
        getattr(
            engine.reasoner,
            "deep_timeout_seconds",
            engine.reasoner.timeout_seconds,
        )
        if reasoning_depth == "deep"
        else engine.reasoner.timeout_seconds
    )
    started = time.perf_counter()
    try:
        remote = engine.run_remote(
            context,
            timeout,
            reasoning_depth,
            template_id,
        )
        analysis = remote["analysis"]
        advice = engine.build_advice(
            context,
            baseline=baseline,
            money_strategy=money,
            analysis=analysis,
            route={
                "requested": "offline_eval",
                "llm_called": True,
                "llm_completed": True,
                "source": analysis.get("source"),
                "template_id": analysis.get("template_id"),
                "template_hash": analysis.get("template_hash"),
                "prompt_hash": analysis.get("prompt_hash"),
                "latency_ms": analysis.get("latency_ms"),
            },
            temporal_quality=str(
                row.get("temporal_quality") or "approximate"
            ),
        )
        if persist:
            save_inference_run(
                data_dir,
                context.get("decision") or {},
                advice,
                analysis_mode="offline_eval",
                reasoning_depth=reasoning_depth,
            )
        action = (advice.get("recommendation") or {}).get("action")
        chosen = _chosen_action(
            data_dir,
            str(row.get("hand_id") or ""),
            int(row.get("sequence") or 0),
        )
        return {
            "valid": True,
            "timeout": False,
            "latency_ms": int(
                analysis.get("latency_ms")
                or round((time.perf_counter() - started) * 1000)
            ),
            "signature": _advice_signature(advice),
            "action_match": (
                action == chosen if action and chosen else None
            ),
            "ev_regret": _money_ev_regret(money, action),
        }
    except (ReasoningError, ValueError, TimeoutError) as error:
        text = str(error)
        if persist:
            template = get_template(template_id)
            save_inference_run(
                data_dir,
                context.get("decision") or {},
                {
                    "schema_version": ADVICE_SCHEMA_VERSION,
                    "route": {
                        "template_id": template.template_id,
                        "template_hash": template.template_hash,
                        "source": getattr(
                            engine.reasoner,
                            "model",
                            "unknown",
                        ),
                    },
                    "context": {
                        "hash": row.get("context_hash"),
                    },
                },
                analysis_mode="offline_eval",
                reasoning_depth=reasoning_depth,
                status="failed",
                validation_status="invalid",
                error_reason=text,
            )
        return {
            "valid": False,
            "timeout": "超时" in text.lower() or "timeout" in text.lower(),
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "error": text,
            "signature": None,
            "action_match": None,
            "ev_regret": None,
        }


def _summarize_template(
    template_id: str,
    subject: str,
    attempts: List[Dict[str, Any]],
    signatures: Dict[str, set],
) -> Dict[str, Any]:
    total = len(attempts)
    valid = [item for item in attempts if item.get("valid")]
    latencies = [float(item["latency_ms"]) for item in valid]
    matches = [
        bool(item["action_match"])
        for item in attempts
        if item.get("action_match") is not None
    ]
    regrets = [
        float(item["ev_regret"])
        for item in valid
        if item.get("ev_regret") is not None
    ]
    stable = [
        len(values) == 1
        for values in signatures.values()
        if values
    ]
    return {
        "template_id": template_id,
        "subject": subject,
        "attempts": total,
        "valid_output_rate": _rate(len(valid), total),
        "timeout_rate": _rate(
            sum(bool(item.get("timeout")) for item in attempts),
            total,
        ),
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "stable_context_rate": _rate(sum(stable), len(stable)),
        "behavior_match_rate": _rate(sum(matches), len(matches)),
        "mean_money_ev_regret": (
            round(statistics.fmean(regrets), 4) if regrets else None
        ),
        "errors": [
            str(item.get("error"))
            for item in attempts
            if item.get("error")
        ][:5],
    }


def _advice_signature(advice: Dict[str, Any]) -> str:
    recommendation = advice.get("recommendation") or {}
    shifts = (
        (advice.get("opponent_exploits") or {}).get("frequency_shifts")
        or []
    )
    shift_signature = sorted(
        (
            item.get("scope"),
            item.get("from_action"),
            item.get("to_action"),
            item.get("delta_pp"),
        )
        for item in shifts
    )
    return repr(
        (
            recommendation.get("action"),
            recommendation.get("raise_to"),
            shift_signature,
        )
    )


def _money_ev_regret(
    money: Dict[str, Any],
    action: Optional[str],
) -> Optional[float]:
    if not action:
        return None
    candidates = money.get("candidates") or []
    values: List[Tuple[str, float]] = []
    for candidate in candidates:
        try:
            values.append(
                (
                    str(candidate.get("action") or ""),
                    float(candidate.get("robust_ev")),
                )
            )
        except (TypeError, ValueError):
            continue
    selected = [value for candidate_action, value in values if candidate_action == action]
    if not values or not selected:
        return None
    return round(max(value for _, value in values) - max(selected), 4)


def _chosen_action(
    data_dir: Path,
    hand_id: str,
    sequence: int,
) -> Optional[str]:
    path = Path(data_dir) / "hands.sqlite3"
    if not path.exists() or not hand_id or not sequence:
        return None
    try:
        with sqlite3.connect(path, timeout=1) as connection:
            row = connection.execute(
                """
                SELECT chosen_action
                FROM decision_snapshots
                WHERE hand_id = ?
                  AND (event_sequence = ? OR action_sequence = ?)
                ORDER BY CASE WHEN event_sequence = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (hand_id, sequence, sequence, sequence),
            ).fetchone()
        return str(row[0]) if row else None
    except sqlite3.Error:
        return None


def _rate(numerator: int, denominator: int) -> Optional[float]:
    return (
        round(100 * numerator / denominator, 1)
        if denominator
        else None
    )


def _percentile(values: List[float], fraction: float) -> Optional[int]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, round((len(ordered) - 1) * fraction)),
    )
    return round(ordered[index])
