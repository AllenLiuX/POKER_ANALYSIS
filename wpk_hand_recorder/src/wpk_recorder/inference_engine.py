from __future__ import annotations

import hashlib
import inspect
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .inference_templates import (
    ADVICE_SCHEMA_VERSION,
    CONTEXT_SCHEMA_VERSION,
    ENGINE_VERSION,
    active_template,
    get_template,
)
from .reasoning import (
    apply_exploit_frequency_shifts,
    local_fast_analysis,
    simplified_range_strategy,
)


def inference_subject(context: Dict[str, Any]) -> str:
    decision = context.get("decision") or {}
    if (
        str(decision.get("decision_subject") or "") == "self"
        and len(decision.get("hero_cards") or []) == 2
    ):
        return "exact_hand"
    return "full_range"


def freeze_inference_context(
    context: Dict[str, Any],
    *,
    temporal_quality: str = "point_in_time",
) -> Dict[str, Any]:
    frozen = deepcopy(context)
    display_names = {}
    profiles = []
    for profile in frozen.get("active_opponent_profiles") or []:
        anonymous = dict(profile)
        player = str(anonymous.get("player") or "")
        display_name = anonymous.pop("display_name", None)
        if player and display_name:
            display_names[player] = str(display_name)
        profiles.append(anonymous)
    frozen["active_opponent_profiles"] = profiles
    frozen.pop("_hand", None)
    frozen.pop("_hero_user_id", None)
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "subject": inference_subject(context),
        "temporal_quality": temporal_quality,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "display_names": display_names,
        "context": frozen,
    }


def inference_context_hash(snapshot: Dict[str, Any]) -> str:
    stable = dict(snapshot)
    stable.pop("captured_at", None)
    stable.pop("display_names", None)
    encoded = json.dumps(
        stable,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class InferenceEngine:
    def __init__(self, reasoner: Any):
        self.reasoner = reasoner

    def run_remote(
        self,
        context: Dict[str, Any],
        timeout_seconds: float,
        reasoning_depth: str,
        template_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        subject = inference_subject(context)
        template = (
            get_template(template_id, subject=subject)
            if template_id
            else active_template(subject)
        )
        method = (
            self.reasoner.analyze
            if subject == "exact_hand"
            else self.reasoner.analyze_exploit
        )
        parameters = inspect.signature(method).parameters
        kwargs: Dict[str, Any] = {}
        if "reasoning_depth" in parameters:
            kwargs["reasoning_depth"] = reasoning_depth
        if "template_id" in parameters:
            kwargs["template_id"] = template.template_id
        analysis = method(context, timeout_seconds, **kwargs)
        analysis.setdefault("template_id", template.template_id)
        analysis.setdefault("template_hash", template.template_hash)
        analysis.setdefault("context_version", CONTEXT_SCHEMA_VERSION)
        return {
            "subject": subject,
            "template": template,
            "analysis": analysis,
        }

    def build_advice(
        self,
        context: Dict[str, Any],
        *,
        baseline: Optional[Dict[str, Any]] = None,
        money_strategy: Optional[Dict[str, Any]] = None,
        analysis: Optional[Dict[str, Any]] = None,
        route: Optional[Dict[str, Any]] = None,
        temporal_quality: str = "point_in_time",
    ) -> Dict[str, Any]:
        subject = inference_subject(context)
        baseline = baseline or simplified_range_strategy(context)
        money_strategy = money_strategy or {}
        analysis = analysis or (
            local_fast_analysis(context) if subject == "exact_hand" else None
        )
        exploit_strategy = (
            apply_exploit_frequency_shifts(baseline, analysis)
            if analysis
            else None
        )
        snapshot = freeze_inference_context(
            context,
            temporal_quality=temporal_quality,
        )
        context_hash = inference_context_hash(snapshot)
        template = active_template(subject)
        reasons = _compact_reasons(analysis)
        if not reasons:
            reasons = [
                {"text": str(item), "evidence": []}
                for item in (
                    money_strategy.get("reasons")
                    or money_strategy.get("caveats")
                    or []
                )[:3]
            ]
        recommendation: Dict[str, Any]
        if subject == "exact_hand":
            money_recommendation = money_strategy.get("recommended") or {}
            money_action = money_recommendation.get("action")
            analysis_action = (analysis or {}).get("recommended_action")
            if money_action and analysis_action != money_action:
                reasons = [
                    {"text": str(item), "evidence": []}
                    for item in money_strategy.get("reasons") or []
                ][:3]
            recommendation = {
                "kind": "exact_action",
                "action": (
                    money_action
                    or analysis_action
                ),
                "raise_to": (
                    money_recommendation.get("raise_to")
                    if money_action
                    else (analysis or {}).get("raise_to")
                ),
            }
        else:
            recommendation = {
                "kind": "full_range",
                "action": None,
                "raise_to": None,
                "summary": (
                    (exploit_strategy or baseline).get("summary")
                    or "按完整范围展示建议频率"
                ),
            }
        route_data = {
            "engine_version": ENGINE_VERSION,
            "context_version": CONTEXT_SCHEMA_VERSION,
            "context_hash": context_hash,
            "template_id": (analysis or {}).get(
                "template_id",
                template.template_id,
            ),
            "template_hash": (analysis or {}).get(
                "template_hash",
                template.template_hash,
            ),
        }
        route_data.update(
            {
                key: value
                for key, value in (route or {}).items()
                if value is not None
            }
        )
        return {
            "schema_version": ADVICE_SCHEMA_VERSION,
            "subject": subject,
            "recommendation": recommendation,
            "reasons": reasons,
            "confidence": (
                (analysis or {}).get("confidence")
                or money_strategy.get("confidence")
                or "low"
            ),
            "range_policy": {
                "baseline": baseline,
                "adjusted": exploit_strategy,
            },
            "opponent_exploits": _opponent_exploits(analysis),
            "money_strategy": money_strategy,
            "route": route_data,
            "context": {
                "hash": context_hash,
                "version": CONTEXT_SCHEMA_VERSION,
                "temporal_quality": temporal_quality,
            },
        }


def _compact_reasons(
    analysis: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if not analysis:
        return []
    reasons = []
    for factor in analysis.get("factors") or []:
        text = str(factor or "").strip()
        if text:
            reasons.append({"text": text, "evidence": []})
    if not reasons:
        for item in analysis.get("opponent_reads") or []:
            text = str(item.get("finding") or "").strip()
            if text:
                reasons.append(
                    {
                        "text": text,
                        "evidence": list(item.get("evidence") or []),
                    }
                )
    summary = str(analysis.get("summary") or "").strip()
    if summary and not reasons:
        reasons.append({"text": summary, "evidence": []})
    return reasons[:3]


def _opponent_exploits(
    analysis: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    analysis = analysis or {}
    return {
        "summary": analysis.get("summary"),
        "reads": list(analysis.get("opponent_reads") or []),
        "range_adjustments": list(
            analysis.get("range_adjustments") or []
        ),
        "frequency_shifts": list(
            analysis.get("frequency_shifts") or []
        ),
        "evidence_details": list(
            analysis.get("evidence_details") or []
        ),
        "caveats": list(analysis.get("caveats") or []),
    }
