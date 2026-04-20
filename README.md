# 🛡️ claude-code-haiku-guard

**A safe-by-default Bash permission guard for Claude Code that uses Claude Haiku to auto-approve routine commands and only prompt you on real risks — so you can run in "accept edits" mode without accidentally letting the agent `rm -rf` your project.**

> [Русская версия →](README.ru.md)

## Who is this for

Claude Code users who:
- work in **"Edit automatically"** mode (file edits go through silently), and
- are tired of **permission dialogs spamming** on every trivial `ls` / `git status`, but
- don't want to blindly allow `Bash(*)` / `Bash(git *)` and watch the agent silently run `git reset --hard` or `rm -rf`.

## ⚠️ Why you need it

Claude Code's default allow-list uses glob patterns. A rule like `Bash(echo *)` matches **any** command starting with `echo`, including `echo ok && rm -rf .git` — because `*` matches spaces, `&&`, and pipes. So every "safe-looking" wildcard in your allow-list is a potential bypass for destructive operations.

**Yes, this really happens** — see [INCIDENTS.md](INCIDENTS.md) for a list of public reports where AI agents (Claude Code, Cursor, Codex) silently executed `rm -rf`, `git reset --hard`, and destructive PowerShell loops in real user projects.

This hook replaces the allow-list with a **risk-based classifier**:

| Risk | What happens |
|------|--------------|
| `none` / `low`   | Silent auto-allow (no dialog) |
| `medium`         | Haiku reads the command in context and decides: silent allow for routine work, dialog for anything suspicious |
| `high` / `critical` | Always prompt — Haiku cannot override |

Classification starts with fast regex rules for ~80 well-known patterns (`rm -rf`, `git push --force`, `kill`, etc.), falls back to Haiku for complex commands, and **floors interpreter wrappers** (`python -c`, `powershell -Command`, `bash -c`, ...) at `medium` so the LLM cannot downgrade arbitrary-code entry points.

## How it works

```
                  ┌─────────────────────────────────────────────────┐
  Bash command ──►│ 1. Rules classifier  (regex, ~80 patterns)      │
                  │ 2. Interpreter floor (medium min for -c / -e)   │
                  │ 3. LLM classifier    (Haiku, for complex/novel) │
                  └─────────┬───────────────────────────────────────┘
                            │
             none/low ──────┼──► allow (silent)
             medium ────────┼──► Haiku decision with cwd + config
                            │    → allow  (routine)
                            │    → ask    (suspicious → dialog)
             high/critical ─┴──► ask (always dialog, Haiku can't override)
```

**Fail-closed by design.** Missing OpenRouter key, network timeout, or LLM error → dialog, not silent allow.

**Cache is command-safe.** Decisions are keyed on full command + `cwd`, so an approval for one command never leaks to a different command that happens to share a prefix.

**Prompt caching ON.** System prompts are sent with `cache_control: ephemeral`, so Anthropic caches them (5-minute TTL). Back-to-back requests pay ~10% of the system-prompt tokens instead of full price.

## 🚀 Install in ~60 seconds

See [SETUP.md](SETUP.md) for the full walkthrough. TL;DR:

1. Copy `hook/haiku_guard.py` to `~/.claude/hooks/haiku_guard.py`
2. Put your OpenRouter key (`sk-or-...`) in `~/.openrouter_key`
3. Merge `examples/settings.json` into `~/.claude/settings.json` — the key piece is a `PreToolUse` hook on `Bash`
4. Remove broad `Bash(X *)` wildcards from your allow-list
5. Restart Claude Code

## Configuration

The hook works out of the box. To teach it about your project-specific "critical artefacts" (files and directories that must always prompt before being deleted or moved), create `~/.claude/hooks/haiku_guard.config.json`:

```json
{
  "critical_files": ["CLAUDE.md", "*.csproj", "MyProject.sln"],
  "critical_dirs": [".claude/", "src/core/", "migrations/"],
  "development_processes": ["dotnet", "node", "python"]
}
```

Missing fields fall back to sensible defaults — see `DEFAULT_CONFIG` in [hook/haiku_guard.py](hook/haiku_guard.py).

Env vars:
- `HAIKU_GUARD_OPENROUTER_KEY` — key inline (overrides key file)
- `HAIKU_GUARD_OPENROUTER_KEY_FILE` — alternative path to key file
- `HAIKU_GUARD_MODEL` — OpenRouter model id (default `anthropic/claude-haiku-4.5`)
- `HAIKU_GUARD_TIMEOUT` — LLM call timeout in seconds (default `15`)

> **Alternative models.** If you can't use Claude (region restriction, preference, cost), swap in Mistral / DeepSeek / Qwen / Llama via `HAIKU_GUARD_MODEL`. See [SETUP.md § Choosing a model](SETUP.md#choosing-a-model) for a candidate list. Comparative benchmarking has not been done yet — expect some cases to flip on switch.

## 🧪 Tests

```bash
cd tests
python test_classifier.py              # 52 offline cases — no API key needed
python test_haiku_decision.py          # 18 Haiku decision cases — requires key
python test_interpreter_destructive.py # 9 interpreter-body cases — requires key
```

The two network-dependent suites skip gracefully when no key is available.

## Cost

A dev session averages ~50-100 Haiku calls/day at 5 tokens output each. OpenRouter pricing for Haiku 4.5 puts this well under $0.10/day.

## Limitations

- **Windows-focused.** Rules and examples assume Windows + Git Bash. On Linux/macOS the classifier still works, but some PowerShell rules are redundant.
- **Claude Code only.** The PreToolUse / PermissionRequest hook contract is Claude Code-specific. Cursor, Codex, Continue etc. use different APIs.
- **OpenRouter dependency.** Falling back to the Haiku decision layer needs a live API key. Without it the guard still classifies via rules and surfaces a dialog on every `medium+` — safe but more clicks.
- **First approval is cached by `cwd`.** If you move the same command to a different directory the cache miss will re-ask Haiku. That is intentional — it's the fix for the "approval-replay" bug.

## License

MIT
