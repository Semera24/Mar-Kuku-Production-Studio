"""Unit tests for TelegramService gateway."""

from pathlib import Path
from mk_studio.telegram.bot import TelegramService


def test_telegram_config_management(tmp_path: Path):
    service = TelegramService(storage_dir=tmp_path)
    
    # Defaults
    cfg = service.get_config()
    assert cfg["enabled"] is False
    assert cfg["bot_token"] == ""
    
    # Save config
    saved = service.save_config(bot_token="12345:ABCDEF", chat_id="998877", enabled=True)
    assert saved["bot_token"] == "12345:ABCDEF"
    assert saved["chat_id"] == "998877"
    assert saved["enabled"] is True
    
    # Re-read
    loaded = service.get_config()
    assert loaded["bot_token"] == "12345:ABCDEF"
    assert loaded["chat_id"] == "998877"
    assert loaded["enabled"] is True
