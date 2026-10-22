"""Governed, auditable macOS desktop and application control package."""

from mk_studio.mac.controller import (
    MAC_CAPABILITY_PROFILES,
    MacApprovalRequiredError,
    MacControlError,
    MacController,
    MacProfileViolationError,
)

__all__ = [
    "MAC_CAPABILITY_PROFILES",
    "MacApprovalRequiredError",
    "MacControlError",
    "MacController",
    "MacProfileViolationError",
]

