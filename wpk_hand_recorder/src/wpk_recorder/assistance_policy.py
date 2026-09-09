from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple


ASSISTANCE_MODES = (
    "operator_approved_live",
    "post_session",
    "static_history",
    "capture_only",
)

CAPABILITIES = (
    "live_range",
    "live_equity",
    "live_advice",
    "profile_analysis",
    "history_range",
)

_MODE_CAPABILITIES = {
    "operator_approved_live": frozenset(CAPABILITIES),
    "post_session": frozenset(
        {"profile_analysis", "history_range"}
    ),
    "static_history": frozenset({"history_range"}),
    "capture_only": frozenset(),
}

_MODE_LABELS = {
    "operator_approved_live": "已确认允许实时辅助",
    "post_session": "仅手后与会话复盘",
    "static_history": "仅静态历史数据",
    "capture_only": "仅录制",
}

_CAPABILITY_LABELS = {
    "live_range": "当前节点对手范围",
    "live_equity": "实时范围权益",
    "live_advice": "实时动作与尺度建议",
    "profile_analysis": "深化画像分析",
    "history_range": "历史范围研究",
}


@dataclass(frozen=True)
class AssistancePolicy:
    mode: str = "operator_approved_live"

    def __post_init__(self) -> None:
        if self.mode not in ASSISTANCE_MODES:
            allowed = ", ".join(ASSISTANCE_MODES)
            raise ValueError(
                f"invalid assistance mode {self.mode!r}; expected one of {allowed}"
            )

    @classmethod
    def from_env(cls, value: Optional[str] = None) -> "AssistancePolicy":
        raw = value
        if raw is None:
            raw = os.environ.get(
                "WPK_ASSISTANCE_MODE",
                "operator_approved_live",
            )
        return cls(str(raw or "").strip().lower())

    def allows(self, capability: str) -> bool:
        if capability not in CAPABILITIES:
            raise ValueError(f"unknown assistance capability {capability!r}")
        return capability in _MODE_CAPABILITIES[self.mode]

    def denial(self, capability: str) -> Tuple[int, str]:
        if self.allows(capability):
            return 200, ""
        label = _CAPABILITY_LABELS[capability]
        if self.mode == "capture_only":
            return 403, f"当前为仅录制模式，未开放{label}"
        if self.mode == "static_history":
            return 403, f"当前为静态历史模式，未开放{label}"
        return 403, f"当前为仅复盘模式，未开放{label}"

    def as_dict(self) -> Dict[str, object]:
        return {
            "mode": self.mode,
            "label": _MODE_LABELS[self.mode],
            "capabilities": {
                capability: self.allows(capability)
                for capability in CAPABILITIES
            },
            "server_enforced": True,
            "browser_mutable": False,
        }
