# Hermes JSON to Markdown Watcher

This daemon converts Hermes JSON session snapshots in `~/.hermes/sessions/*.json`
into Obsidian source-record notes at:

`~/Projects/Workspace-Monorepo/vault/Logs/Hermes/YYYY-MM-DD.md`

Each daily note gets frontmatter for the Source Freshness, Second Brain Source
Records, and Decisions Without Outcomes bases.

## Requirements

- Hermes session JSON snapshots enabled.
- `ANTHROPIC_API_KEY` set in the environment or `~/.hermes/.env`.
- The Hermes Python environment with the Anthropic extra installed.
- Optional: `watchdog` for macOS fsevents. Without it, the daemon polls every
  60 seconds.

## Manual Run

```bash
cd ~/Projects/Active/hermes-agent
source .venv/bin/activate  # or venv/bin/activate
python watcher/json_to_md.py --once
python watcher/json_to_md.py
```

If this checkout shares Hermes' installed virtualenv, use the probe script:

```bash
watcher/run_json_to_md.sh --once
```

Useful options:

- `--poll-only` forces the 60-second polling path.
- `--settle-seconds 60` waits until a JSON file has stopped changing before
  converting it.
- `--model claude-haiku-4-5` overrides the default extraction model.

The processed-file cache lives at `~/.hermes/sessions/.processed.json`.
Errors are logged to `~/.hermes/watcher.log`.

## LaunchAgent Management

```bash
hermes watcher install      # write, validate, and load the LaunchAgent
hermes watcher status       # show loaded state, PID/exit status, and log paths
hermes watcher unload       # stop the LaunchAgent without deleting the plist
hermes watcher load         # load or reload the installed LaunchAgent
hermes watcher validate     # validate the installed plist
```

The installed plist lives at:

`~/Library/LaunchAgents/com.ryangherardi.hermes-watcher.plist`

Status output includes these troubleshooting paths:

- watcher log: `~/.hermes/watcher.log`
- launchd stdout: `~/.hermes/watcher.launchd.out.log`
- launchd stderr: `~/.hermes/watcher.launchd.err.log`
- processed cache: `~/.hermes/sessions/.processed.json`
- Markdown output: `~/Projects/Workspace-Monorepo/vault/Logs/Hermes/`

## Verification

1. Start a Hermes test session and send a few messages.
2. Exit the session.
3. Wait about 90 seconds.
4. Confirm `~/Projects/Workspace-Monorepo/vault/Logs/Hermes/YYYY-MM-DD.md`
   has a new `Hermes session` section and source-record frontmatter.
5. Run the daemon again with `--once`; the same session should not duplicate.
6. Refresh the Obsidian bases.
