# Real-world AI coding-agent incidents

**AI coding agents really do destroy user work in production. Not through exotic jailbreaks — through plain admin commands that slipped past the permission dialog.** The incidents below are all publicly documented across Claude Code, Cursor, and Codex within the last year. The patterns repeat: recursive delete, destructive git, shell-wrapper abuse, process kills.

> [Русская версия →](INCIDENTS.ru.md)

## The usual suspects

Five classes of command are responsible for almost every reported incident:

- `rm -rf` (and `Remove-Item -Recurse -Force` on Windows)
- `git reset --hard` / `git checkout -- .` / `git restore` / `git clean`
- shell and interpreter wrappers that allow arbitrary code: `bash -c`, `python -c`, `powershell -Command`
- process kills: `kill`, `pkill`, `killall`, `taskkill`
- mass PowerShell destructive loops: `Clear-RecycleBin -Force`, nested `Remove-Item`

The common failure mode is the same: an allow-list rule matched a "safe-looking" prefix, the real guard never ran, and the destructive tail of the command executed silently.

## Public reports

### Claude Code

- **2025-08-26** — `rm -rf` executed without approval.
  <https://github.com/anthropics/claude-code/issues/6608>
- **2025-09-06** — `git reset --hard cff6f72` followed by `git checkout -- .`. Uncommitted work lost.
  <https://github.com/anthropics/claude-code/issues/7232>
- **2025-10-21** — home-directory removal scenario consistent with `rm -rf` from `/`. Exact command not logged, evidence matches the pattern.
  <https://github.com/anthropics/claude-code/issues/10077>

### Codex

- **2025-12-31** — `git restore` executed despite an explicit "never touch git" instruction.
  <https://github.com/openai/codex/issues/8643>
- **2026-02-19** — mass file deletion; evidence shows `Clear-RecycleBin -Force` and nested `Remove-Item` loops.
  <https://github.com/openai/codex/issues/12277>

### Cursor

- **2025-12-08** — `rm -rf` across git-tracked directories, plus `pkill`.
  <https://forum.cursor.com/t/catastrophic-damage-and-chaos-in-plan-mode/145523>
- **2026-03-14** — staff-confirmed bug: deletions outside the workspace without confirmation.
  <https://forum.cursor.com/t/agents-deleting-files-outside-workspace-without-confirmation/154768>
- **2026-03-26** — staff-confirmed bug: allow-list not applied in sandbox auto-run as users expected.
  <https://forum.cursor.com/t/agent-did-remove-file-without-confirmation/155925>

## Why this hook exists

The failure surface is predictable and the list of dangerous patterns is small. A guard doesn't have to be clever — it has to make sure **no allow-list rule can bypass classification**, and that destructive command shapes are always surfaced to the user regardless of whether the specific args happen to be a no-op.

That is exactly what this repo implements.

## Caveat

Public issue threads are not always fully investigated post-mortems. But they are useful as a floor for defensive policy: the same command classes appear again and again, regardless of which agent and which IDE. If a guard doesn't block these, users will eventually hit them.
