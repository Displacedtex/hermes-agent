import json
import os
import time
from pathlib import Path

from watcher import json_to_md


def _write_session(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "session_id": "session-123",
                "last_updated": "2026-05-31T10:15:00",
                "messages": [
                    {"role": "system", "content": "do not include me"},
                    {"role": "user", "content": "We decided to build the Hermes watcher."},
                    {"role": "assistant", "content": "Action item: install it with launchd."},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_process_file_writes_source_record_frontmatter_and_cache(tmp_path):
    sessions_dir = tmp_path / "sessions"
    vault_dir = tmp_path / "vault"
    sessions_dir.mkdir()
    session_path = sessions_dir / "session_session-123.json"
    _write_session(session_path)
    cache_path = sessions_dir / ".processed.json"

    facts = {
        "decisions": ["Build the Hermes watcher."],
        "facts": ["Hermes writes JSON snapshots."],
        "action_items": ["Install it with launchd."],
        "questions": [],
    }

    assert json_to_md.process_file(
        session_path,
        vault_dir=vault_dir,
        cache_path=cache_path,
        extractor=lambda _transcript: facts,
    )

    note = vault_dir / "Logs" / "Hermes" / "2026-05-31.md"
    content = note.read_text(encoding="utf-8")
    assert "source-record" in content
    assert "hermes-session" in content
    assert "decision" in content
    assert 'source_system: "hermes-agent"' in content
    assert "## 2026-05-31T10:15:00" in content
    assert "- Build the Hermes watcher." in content
    assert "- Install it with launchd." in content

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert str(session_path.resolve()) in cache["processed"]


def test_scan_once_does_not_duplicate_processed_sessions(tmp_path):
    sessions_dir = tmp_path / "sessions"
    vault_dir = tmp_path / "vault"
    sessions_dir.mkdir()
    session_path = sessions_dir / "session_session-123.json"
    _write_session(session_path)
    old_time = time.time() - 120
    os.utime(session_path, (old_time, old_time))
    cache_path = sessions_dir / ".processed.json"

    facts = {
        "decisions": [],
        "facts": ["One fact."],
        "action_items": [],
        "questions": [],
    }

    kwargs = {
        "sessions_dir": sessions_dir,
        "vault_dir": vault_dir,
        "cache_path": cache_path,
        "extractor": lambda _transcript: facts,
        "settle_seconds": 60,
    }

    assert json_to_md.scan_once(**kwargs) == 1
    assert json_to_md.scan_once(**kwargs) == 0

    note = vault_dir / "Logs" / "Hermes" / "2026-05-31.md"
    content = note.read_text(encoding="utf-8")
    assert content.count("Hermes session `session-123`") == 1


def test_parse_request_dump_uses_nested_messages_only(tmp_path):
    path = tmp_path / "request_dump_abc.json"
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-05-31T11:00:00",
                "session_id": "abc",
                "request": {
                    "headers": {"Authorization": "Bearer secret"},
                    "body": {
                        "messages": [
                            {"role": "system", "content": "secret system prompt"},
                            {"role": "user", "content": [{"type": "text", "text": "remember this fact"}]},
                        ]
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    record = json_to_md.parse_session_file(path)

    assert record is not None
    assert record.session_id == "abc"
    assert "remember this fact" in record.transcript
    assert "Bearer secret" not in record.transcript
    assert "secret system prompt" not in record.transcript
