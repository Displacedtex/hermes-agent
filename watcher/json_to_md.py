#!/usr/bin/env python3
"""Convert Hermes JSON session snapshots into Obsidian source records."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


EXTRACTION_PROMPT = """Extract key facts from this conversation. Output ONLY valid JSON with these keys (use empty list if none):
{
  "decisions": ["..."],
  "facts": ["..."],
  "action_items": ["..."],
  "questions": ["..."]
}
Be terse, factual, and suitable for an Obsidian source-record note. No prose, no markdown, no explanation. JSON only."""

DEFAULT_EMPTY_EXTRACTION = {
    "decisions": [],
    "facts": [],
    "action_items": [],
    "questions": [],
}

FRONTMATTER_BASE = {
    "source_system": "hermes-agent",
    "freshness_status": "ok",
    "trust_status": "verified",
    "status": "ok",
    "domain": "agent-conversations",
}


@dataclass(frozen=True)
class SessionRecord:
    path: Path
    session_id: str
    timestamp: datetime
    transcript: str
    message_count: int


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def default_sessions_dir() -> Path:
    return hermes_home() / "sessions"


def default_vault_dir() -> Path:
    return Path.home() / "Projects" / "Workspace-Monorepo" / "vault"


def default_log_path() -> Path:
    return hermes_home() / "watcher.log"


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or item.get("input")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        text = content.get("text") or content.get("content")
        if isinstance(text, str):
            return text
    return str(content)


def messages_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(payload.get("messages"), list):
        return payload["messages"]

    body = payload.get("request", {}).get("body")
    if isinstance(body, dict) and isinstance(body.get("messages"), list):
        return body["messages"]

    return []


def parse_session_file(path: Path) -> SessionRecord | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None

    messages = messages_from_payload(payload)
    transcript_lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "unknown")
        if role == "system":
            continue
        text = content_to_text(msg.get("content")).strip()
        if not text:
            continue
        transcript_lines.append(f"{role.upper()}:\n{text}")

    if not transcript_lines:
        return None

    raw_timestamp = payload.get("last_updated") or payload.get("timestamp") or payload.get("session_start")
    timestamp = parse_timestamp(raw_timestamp) or datetime.fromtimestamp(path.stat().st_mtime)
    session_id = str(payload.get("session_id") or path.stem)
    return SessionRecord(
        path=path,
        session_id=session_id,
        timestamp=timestamp,
        transcript="\n\n---\n\n".join(transcript_lines),
        message_count=len(transcript_lines),
    )


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return parsed.astimezone().replace(tzinfo=None)
    return parsed


def strip_json_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text
    text = text.split("```", 1)[1]
    if text.lstrip().startswith("json"):
        text = text.lstrip()[4:]
    return text.rsplit("```", 1)[0].strip()


def normalize_extraction(data: Any) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key in DEFAULT_EMPTY_EXTRACTION:
        values = data.get(key, []) if isinstance(data, dict) else []
        if not isinstance(values, list):
            values = [values]
        normalized[key] = [str(item).strip() for item in values if str(item).strip()]
    return normalized


def extract_with_claude(transcript: str, model: str) -> dict[str, list[str]]:
    try:
        from anthropic import Anthropic
    except ImportError as exc:
        raise RuntimeError("Install the anthropic extra before running the watcher.") from exc

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment or ~/.hermes/.env.")

    client = Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[
            {
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n\n---\nConversation:\n{transcript[:50000]}",
            }
        ],
    )
    raw = response.content[0].text
    return normalize_extraction(json.loads(strip_json_fence(raw)))


def load_processed(cache_path: Path) -> dict[str, Any]:
    if not cache_path.exists():
        return {"processed": {}}
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        logging.exception("Failed to read processed cache %s; starting with an empty cache", cache_path)
        return {"processed": {}}
    if not isinstance(cache, dict) or not isinstance(cache.get("processed"), dict):
        return {"processed": {}}
    return cache


def save_processed(cache_path: Path, cache: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(cache_path)


def cache_key(path: Path) -> str:
    return str(path.expanduser().resolve())


def is_stable(path: Path, settle_seconds: int) -> bool:
    try:
        return time.time() - path.stat().st_mtime >= settle_seconds
    except FileNotFoundError:
        return False


def yaml_scalar(value: str | None) -> str:
    if value is None:
        return "null"
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_frontmatter(tags: list[str], date: str, latest_session_id: str, latest_session_at: str) -> str:
    lines = ["---"]
    lines.append("tags:")
    for tag in tags:
        lines.append(f"  - {tag}")
    for key, value in FRONTMATTER_BASE.items():
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append(f"date: {yaml_scalar(date)}")
    lines.append(f"latest_session_id: {yaml_scalar(latest_session_id)}")
    lines.append(f"latest_session_at: {yaml_scalar(latest_session_at)}")
    if "decision" in tags:
        lines.append("outcome: null")
        lines.append(f"evidence_uri: {yaml_scalar(f'hermes-session:{latest_session_id}')}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    raw = content[4:end]
    body = content[end + len("\n---\n") :]
    meta: dict[str, Any] = {}
    current_list_key: str | None = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list_key:
            meta.setdefault(current_list_key, []).append(line[4:].strip().strip('"'))
            continue
        current_list_key = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not value:
            meta[key] = []
            current_list_key = key
        elif value.startswith("[") and value.endswith("]"):
            meta[key] = [item.strip().strip('"').strip("'") for item in value[1:-1].split(",") if item.strip()]
        elif value == "null":
            meta[key] = None
        else:
            meta[key] = value.strip('"')
    return meta, body.lstrip("\n")


def ensure_frontmatter(path: Path, record: SessionRecord, has_decisions: bool) -> str:
    if path.exists():
        meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    else:
        meta, body = {}, f"# Hermes Sessions — {record.timestamp:%Y-%m-%d}\n\n"

    tags = list(dict.fromkeys([*(meta.get("tags") or []), "source-record", "hermes-session"]))
    if has_decisions and "decision" not in tags:
        tags.append("decision")

    frontmatter = render_frontmatter(
        tags=tags,
        date=record.timestamp.strftime("%Y-%m-%d"),
        latest_session_id=record.session_id,
        latest_session_at=record.timestamp.isoformat(timespec="seconds"),
    )
    return frontmatter + body


def markdown_list(items: list[str]) -> str:
    if not items:
        return "- None captured.\n"
    return "".join(f"- {item}\n" for item in items)


def render_session_block(record: SessionRecord, facts: dict[str, list[str]]) -> str:
    source = record.path.name
    timestamp = record.timestamp.isoformat(timespec="seconds")
    return (
        f"\n## {timestamp} — Hermes session `{record.session_id}`\n\n"
        f"- Source JSON: `{source}`\n"
        f"- Messages extracted: {record.message_count}\n\n"
        "### Decisions\n"
        f"{markdown_list(facts.get('decisions', []))}\n"
        "### Facts\n"
        f"{markdown_list(facts.get('facts', []))}\n"
        "### Action Items\n"
        f"{markdown_list(facts.get('action_items', []))}\n"
        "### Questions\n"
        f"{markdown_list(facts.get('questions', []))}\n"
    )


def append_to_daily_note(vault_dir: Path, record: SessionRecord, facts: dict[str, list[str]]) -> Path:
    target_dir = vault_dir / "Logs" / "Hermes"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{record.timestamp:%Y-%m-%d}.md"

    content = ensure_frontmatter(target, record, bool(facts.get("decisions")))
    content = content.rstrip() + "\n" + render_session_block(record, facts)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(target)
    return target


def process_file(
    path: Path,
    *,
    vault_dir: Path,
    cache_path: Path,
    extractor: Callable[[str], dict[str, list[str]]],
) -> bool:
    cache = load_processed(cache_path)
    key = cache_key(path)
    if key in cache["processed"]:
        return False

    record = parse_session_file(path)
    if record is None:
        logging.info("Skipping %s because no session messages were found", path)
        cache["processed"][key] = {
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "status": "skipped-no-messages",
        }
        save_processed(cache_path, cache)
        return False

    facts = extractor(record.transcript)
    target = append_to_daily_note(vault_dir, record, facts)
    cache["processed"][key] = {
        "processed_at": datetime.now().isoformat(timespec="seconds"),
        "session_id": record.session_id,
        "target": str(target),
        "size": path.stat().st_size,
        "mtime": path.stat().st_mtime,
    }
    save_processed(cache_path, cache)
    logging.info("Converted %s to %s", path, target)
    return True


def scan_once(
    *,
    sessions_dir: Path,
    vault_dir: Path,
    cache_path: Path,
    extractor: Callable[[str], dict[str, list[str]]],
    settle_seconds: int,
) -> int:
    processed = 0
    sessions_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(sessions_dir.glob("*.json")):
        if path.name == cache_path.name:
            continue
        if not is_stable(path, settle_seconds):
            continue
        try:
            if process_file(path, vault_dir=vault_dir, cache_path=cache_path, extractor=extractor):
                processed += 1
        except Exception:
            logging.exception("Failed to process %s", path)
    return processed


def run_polling(args: argparse.Namespace, extractor: Callable[[str], dict[str, list[str]]]) -> None:
    stop = False

    def _stop(_signum: int, _frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    while not stop:
        scan_once(
            sessions_dir=args.sessions_dir,
            vault_dir=args.vault_dir,
            cache_path=args.cache_path,
            extractor=extractor,
            settle_seconds=args.settle_seconds,
        )
        if args.once:
            break
        time.sleep(args.poll_interval)


def run_watchdog(args: argparse.Namespace, extractor: Callable[[str], dict[str, list[str]]]) -> bool:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return False

    class Handler(FileSystemEventHandler):
        def on_created(self, event: Any) -> None:
            self._scan_if_json(event)

        def on_modified(self, event: Any) -> None:
            self._scan_if_json(event)

        def _scan_if_json(self, event: Any) -> None:
            if event.is_directory or not str(event.src_path).endswith(".json"):
                return
            scan_once(
                sessions_dir=args.sessions_dir,
                vault_dir=args.vault_dir,
                cache_path=args.cache_path,
                extractor=extractor,
                settle_seconds=args.settle_seconds,
            )

    args.sessions_dir.mkdir(parents=True, exist_ok=True)
    observer = Observer()
    observer.schedule(Handler(), str(args.sessions_dir), recursive=False)
    observer.start()
    logging.info("Watching %s with watchdog", args.sessions_dir)
    try:
        scan_once(
            sessions_dir=args.sessions_dir,
            vault_dir=args.vault_dir,
            cache_path=args.cache_path,
            extractor=extractor,
            settle_seconds=args.settle_seconds,
        )
        if args.once:
            return True
        while True:
            time.sleep(args.poll_interval)
            scan_once(
                sessions_dir=args.sessions_dir,
                vault_dir=args.vault_dir,
                cache_path=args.cache_path,
                extractor=extractor,
                settle_seconds=args.settle_seconds,
            )
    except KeyboardInterrupt:
        return True
    finally:
        observer.stop()
        observer.join()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-dir", type=Path, default=default_sessions_dir())
    parser.add_argument("--vault-dir", type=Path, default=default_vault_dir())
    parser.add_argument("--cache-path", type=Path, default=default_sessions_dir() / ".processed.json")
    parser.add_argument("--log-path", type=Path, default=default_log_path())
    parser.add_argument("--model", default=os.environ.get("HERMES_WATCHER_MODEL", "claude-haiku-4-5"))
    parser.add_argument("--poll-interval", type=int, default=60)
    parser.add_argument("--settle-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_path)
    load_env_file(hermes_home() / ".env")

    extractor = lambda transcript: extract_with_claude(transcript, args.model)
    logging.info("Starting Hermes JSON to Markdown watcher")
    if not args.poll_only and run_watchdog(args, extractor):
        return 0
    if not args.poll_only:
        logging.info("watchdog is not installed; falling back to polling every %s seconds", args.poll_interval)
    run_polling(args, extractor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
