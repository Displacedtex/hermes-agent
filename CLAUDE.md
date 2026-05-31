# Hermes Agent Local Notes

## JSON to Markdown Watcher

`watcher/json_to_md.py` converts Hermes JSON session snapshots from
`~/.hermes/sessions/*.json` into Obsidian source-record Markdown notes under
`~/Projects/Workspace-Monorepo/vault/Logs/Hermes/YYYY-MM-DD.md`.

The watcher:

- Uses `watchdog` for fsevents when available, otherwise polls every 60 seconds.
- Waits for each JSON file to be stable before conversion.
- Calls Claude Haiku with a structured extraction prompt.
- Appends multiple sessions from the same day into one daily note.
- Maintains `~/.hermes/sessions/.processed.json` to avoid duplicate sections.
- Logs daemon errors to `~/.hermes/watcher.log`.

Install the LaunchAgent from
`launchd/com.ryangherardi.hermes-watcher.plist` by copying it to
`~/Library/LaunchAgents/` and loading it with `launchctl load`.

Run a one-shot conversion during development:

```bash
cd ~/Projects/Active/hermes-agent
source .venv/bin/activate
python watcher/json_to_md.py --once
```
