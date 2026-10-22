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
