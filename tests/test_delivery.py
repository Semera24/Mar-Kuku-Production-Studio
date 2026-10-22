"""Delivery must not invent successful uploads or blindly retry an uncertain upload."""
import json
import threading
from pathlib import Path
import pytest
from mk_studio.config import RootPolicy
from mk_studio.delivery import YouTubeMetadata, YouTubeDelivery


def test_default_visibility_is_private():
    m = YouTubeMetadata(title='Mar and Kuku', description='A test', made_for_kids=True)
    assert m.visibility == 'private'


def test_unknown_upload_blocks_retry(tmp_path):
    folder = tmp_path / 'scratch'
    folder.mkdir()
    video = folder / 'video.mp4'
    video.write_bytes(b'existing registered video')
    receipt = folder / 'youtube-upload.json'
    receipt.write_text(json.dumps({'status': 'submitting', 'video_path': str(video)}))
    delivery = YouTubeDelivery(RootPolicy(output_root=folder), browser=None)
    with pytest.raises(RuntimeError, match='(?i)reconcil|uncertain'):
        delivery.upload(video, YouTubeMetadata(title='Test', made_for_kids=True), receipt)


def test_empty_video_rejected_before_browser(tmp_path):
    folder = tmp_path / 'scratch'
    folder.mkdir()
    video = folder / 'video.mp4'
    video.write_bytes(b'')
    delivery = YouTubeDelivery(RootPolicy(output_root=folder), browser=None)
    with pytest.raises(ValueError, match='(?i)empty'):
        delivery.upload(video, YouTubeMetadata(title='Test', made_for_kids=True), folder / 'upload.json')


def test_metadata_bounds():
    with pytest.raises(ValueError):
        YouTubeMetadata(title='x' * 101, made_for_kids=True)


def test_youtube_delivery_successful_private_upload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that YouTubeDelivery executes the CDP sequence and records a private uploaded video URL."""
    folder = tmp_path / "scratch"
    folder.mkdir()
    video = folder / "chapter.mp4"
    video.write_bytes(b"\x00\x00\x00 ftypisom" + b"\x00" * 1024)

    receipt = folder / "09_youtube_upload_receipt.json"

    # Mock service.verify_video
    monkeypatch.setattr(
        "mk_studio.production.service.ProductionService.verify_video",
        lambda self, v, frames: {"full_decode_passed": True, "video_frames": frames},
    )

    # Mock subprocess ffprobe in verify
    fake_probe = json.dumps({"streams": [{"codec_type": "video", "nb_read_frames": "900"}]})
    from unittest.mock import MagicMock
    mock_run = MagicMock()
    mock_run.stdout = fake_probe
    monkeypatch.setattr(
        "mk_studio.production.service.ProductionService._run",
        lambda self, cmd: mock_run,
    )

    # Mock browser and CDP client
    class MockBrowser:
        cancel_event = threading.Event()
        def _require_not_in_takeover(self): pass
        def _check_cancel(self): pass
        def open_url(self, url, wait_seconds=12): pass
        def _get_or_create_page_target(self):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/test"}

    class MockWs:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call(self, *args, **kwargs): return {}

    monkeypatch.setattr("mk_studio.delivery.CdpWebSocketClient", lambda url: MockWs())

    delivery = YouTubeDelivery(RootPolicy(output_root=folder), browser=MockBrowser())

    # Mock _wait, _eval, _file
    monkeypatch.setattr(delivery, "_wait", lambda ws, src, *args: "https://youtu.be/AbCdEfGhIjK" if "youtu" in src else True)
    monkeypatch.setattr(delivery, "_eval", lambda ws, src: True)
    monkeypatch.setattr(delivery, "_file", lambda ws, sel, p: True)

    meta = YouTubeMetadata(
        title="Mar and Kuku: Flower Adventure",
        description="Preschool counting episode.",
        made_for_kids=True,
        visibility="private",
    )

    res = delivery.upload(video, meta, receipt)
    assert res["status"] == "uploaded"
    assert res["video_url"] == "https://youtu.be/AbCdEfGhIjK"
    assert res["visibility"] == "private"
    assert Path(receipt).is_file()

