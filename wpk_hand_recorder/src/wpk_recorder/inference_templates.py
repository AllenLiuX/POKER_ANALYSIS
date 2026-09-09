from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


CONTEXT_SCHEMA_VERSION = "inference-context-v1"
ADVICE_SCHEMA_VERSION = "inference-advice-v1"
ENGINE_VERSION = "unified-inference-v1"


@dataclass(frozen=True)
class InferenceTemplate:
    template_id: str
    subject: str
    description: str
    instruction_suffix: str
    production_ready: bool = False

    @property
    def template_hash(self) -> str:
        encoded = json.dumps(
            asdict(self),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


_TEMPLATES: Dict[str, InferenceTemplate] = {
    "exact-action-v1": InferenceTemplate(
        template_id="exact-action-v1",
        subject="exact_hand",
        description="精确手牌：本地事实优先、证据约束、简短行动建议",
        instruction_suffix=(
            "最终建议先满足合法动作与本地 Money-EV 约束；"
            "理由最多三条，按决策影响从高到低排列。"
        ),
        production_ready=True,
    ),
    "full-range-v1": InferenceTemplate(
        template_id="full-range-v1",
        subject="full_range",
        description="观战范围：不猜底牌、输出证据化范围频率调整",
        instruction_suffix=(
            "不得猜测具体底牌或输出单手牌动作；"
            "优先返回可执行的范围桶频率转移，理由最多三条。"
        ),
        production_ready=True,
    ),
    "exact-action-evidence-v2": InferenceTemplate(
        template_id="exact-action-evidence-v2",
        subject="exact_hand",
        description="候选模板：先列证据与反证，再给精确动作",
        instruction_suffix=(
            "先内部检查支持建议的事实、反证和样本限制，再输出 JSON；"
            "只在证据一致时改变本地基线，理由最多三条。"
        ),
    ),
    "full-range-evidence-v2": InferenceTemplate(
        template_id="full-range-evidence-v2",
        subject="full_range",
        description="候选模板：按证据强度排序范围调整",
        instruction_suffix=(
            "先内部按样本量、偏移幅度和场景匹配度排序证据；"
            "只输出最高价值的范围频率转移，理由最多三条。"
        ),
    ),
}


def template_ids(subject: Optional[str] = None) -> List[str]:
    return [
        template_id
        for template_id, template in _TEMPLATES.items()
        if subject is None or template.subject == subject
    ]


def get_template(
    template_id: str,
    *,
    subject: Optional[str] = None,
) -> InferenceTemplate:
    template = _TEMPLATES.get(str(template_id or ""))
    if template is None:
        raise ValueError(f"未知 inference 模板：{template_id}")
    if subject is not None and template.subject != subject:
        raise ValueError(
            f"模板 {template_id} 不适用于 {subject}"
        )
    return template


def active_template(subject: str) -> InferenceTemplate:
    if subject == "exact_hand":
        configured = os.getenv(
            "WPK_INFERENCE_EXACT_TEMPLATE",
            "exact-action-v1",
        )
    elif subject == "full_range":
        configured = os.getenv(
            "WPK_INFERENCE_RANGE_TEMPLATE",
            "full-range-v1",
        )
    else:
        raise ValueError(f"未知 inference subject：{subject}")
    template = get_template(configured, subject=subject)
    if not template.production_ready and os.getenv(
        "WPK_INFERENCE_ALLOW_EXPERIMENTAL",
        "",
    ).strip().lower() not in {"1", "true", "yes"}:
        raise ValueError(
            f"模板 {configured} 尚未晋升为 production_ready"
        )
    return template
