"""Visible controlled browser package (Chrome DevTools Protocol)."""

from mk_studio.browser.controller import (
    BrowserController,
    BrowserControlError,
    BrowserTakeoverActiveError,
)
from mk_studio.browser.flow_music import GoogleFlowMusicAdapter

__all__ = [
    "BrowserController",
    "BrowserControlError",
    "BrowserTakeoverActiveError",
    "GoogleFlowMusicAdapter",
]

