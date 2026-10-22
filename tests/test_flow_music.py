"""Unit tests for Google Flow Music (Lyria 3.5) browser adapter."""

from pathlib import Path
from unittest.mock import MagicMock
import pytest

from mk_studio.browser import GoogleFlowMusicAdapter
from mk_studio.browser.controller import BrowserControlError


def test_check_connection_and_auth_authenticated() -> None:
    mock_browser = MagicMock()
    mock_browser.is_reachable.return_value = True
    mock_browser.inspect_dom.return_value = {
        "url": "https://www.flowmusic.app/",
        "bodyTextExcerpt": "Sessions Colour Garden Adventure Five Flowers Remix dancingPatina PLUS",
        "buttons": [{"text": "dancingPatina PLUS"}, {"text": "New session"}],
    }

    adapter = GoogleFlowMusicAdapter(mock_browser)
    res = adapter.check_connection_and_auth()

    assert res["authenticated"]
    assert res["account_handle"] == "dancingPatina PLUS"
    assert not res["login_required"]
    assert res["has_reference_sessions"]


def test_check_connection_and_auth_logged_out() -> None:
    mock_browser = MagicMock()
    mock_browser.is_reachable.return_value = True
    mock_browser.inspect_dom.return_value = {
        "url": "https://www.flowmusic.app/",
        "bodyTextExcerpt": "Welcome to the studio Make any song",
        "buttons": [{"text": "Log in"}, {"text": "Sign up"}],
    }

    adapter = GoogleFlowMusicAdapter(mock_browser)
    res = adapter.check_connection_and_auth()

    assert not res["authenticated"]
    assert res["login_required"]
    assert res["account_handle"] is None


def test_open_reference_session() -> None:
    mock_browser = MagicMock()
    mock_browser.capture_screenshot.return_value = {"path": "/fake/shot.png"}
    mock_browser.inspect_dom.return_value = {
        "url": "https://www.flowmusic.app/session/c6d54460-584e-457b-97e2-5a9ee8189e74",
        "title": "Google Flow Music",
        "bodyTextExcerpt": "Five Flowers Remix Welcome to the studio",
    }

    adapter = GoogleFlowMusicAdapter(mock_browser)
    res = adapter.open_reference_session()

    assert res["has_five_flowers"]
    assert res["screenshot"] == "/fake/shot.png"
    mock_browser.open_url.assert_called_once()


def test_list_session_tracks(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_browser = MagicMock()
    mock_browser._get_or_create_page_target.return_value = {"webSocketDebuggerUrl": "ws://fake"}

    class FakeClient:
        def __init__(self, ws_url: str) -> None:
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def call(self, method: str, params: dict | None = None):
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "value": [
                            {"name": "Five Flowers in the Garden", "has_more_options": True, "has_remix": True},
                            {"name": "Nine Birds in the Sky", "has_more_options": True, "has_remix": True},
                        ]
                    }
                }
            return {}

    monkeypatch.setattr("mk_studio.browser.flow_music.CdpWebSocketClient", FakeClient)
    adapter = GoogleFlowMusicAdapter(mock_browser)
    tracks = adapter.list_session_tracks()

    assert len(tracks) == 2
    assert tracks[0]["name"] == "Five Flowers in the Garden"
    assert tracks[1]["name"] == "Nine Birds in the Sky"


def test_download_track_audio(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mock_browser = MagicMock()
    mock_browser._get_or_create_page_target.return_value = {"webSocketDebuggerUrl": "ws://fake"}
    mock_browser.capture_screenshot.return_value = {"path": str(tmp_path / "shot.png")}
    mock_browser.policy.output_root_resolved = tmp_path / "output"

    downloads_dir = tmp_path / "Downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    fake_wav_content = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00\x80\xbb\x00\x00\x00\xee\x02\x00\x04\x00\x10\x00data\x00\x00\x00\x00"

    class FakeClient:
        def __init__(self, ws_url: str) -> None:
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def call(self, method: str, params: dict | None = None):
            if method == "Runtime.evaluate":
                expr = (params or {}).get("expression", "")
                if "targetFmt" in expr:
                    # Simulate browser downloading the file when format is clicked
                    target_file = downloads_dir / "Nine Birds in the Sky.wav"
                    target_file.write_bytes(fake_wav_content)
                return {"result": {"value": {"ok": True}}}
            return {}

    monkeypatch.setattr("mk_studio.browser.flow_music.CdpWebSocketClient", FakeClient)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    dest_dir = tmp_path / "dest"
    adapter = GoogleFlowMusicAdapter(mock_browser)
    res = adapter.download_track_audio(
        track_name="Nine Birds in the Sky",
        download_format="WAV",
        destination_dir=dest_dir,
        timeout_seconds=5.0,
    )

    assert res["track_name"] == "Nine Birds in the Sky"
    assert res["format"] == "WAV"
    assert Path(res["saved_path"]).is_file()
    assert res["size_bytes"] == len(fake_wav_content)

