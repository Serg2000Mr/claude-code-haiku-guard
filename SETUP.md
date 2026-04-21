# 🔧 Setup

This guide gets the guard working first. Optional model tuning comes later.

> [Русская версия →](SETUP.ru.md)

## 🔑 1. Create an OpenRouter key

The guard calls OpenRouter, so an OpenAI or Anthropic key will not work here.

1. Go to <https://openrouter.ai/settings/keys>
2. Create a key that starts with `sk-or-...`
3. Save it to `~/.openrouter_key`:

```bash
echo "sk-or-v1-..." > ~/.openrouter_key
chmod 600 ~/.openrouter_key  # Linux/macOS
```

On Windows (Git Bash), `~` resolves to `C:\Users\<you>`.

You can also set `HAIKU_GUARD_OPENROUTER_KEY` directly in Claude Code's `settings.json`.

## 📦 2. Install the hook file

```bash
mkdir -p ~/.claude/hooks
cp hook/haiku_guard.py ~/.claude/hooks/haiku_guard.py
```

Quick standalone check:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls /tmp"}}' \
  | python ~/.claude/hooks/haiku_guard.py
```

Expected output:

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"auto: none: read-only"}}
```

If Python fails here, fix that first before wiring the hook into Claude Code.

## ⚙️ 3. Add the hook to `settings.json`

Merge the `hooks` block from [examples/settings.json](examples/settings.json) into `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/hooks/haiku_guard.py",
            "timeout": 70
          }
        ]
      }
    ]
  }
}
```

Use a Bash-scoped `PreToolUse` hook for the main flow in this repo. The script also understands `PermissionRequest`, but `PreToolUse` is the path you want for normal Bash classification.

Even with `PreToolUse`, you should still remove broad Bash allow rules from both global and project settings. Otherwise Claude Code can approve commands so broadly that this guard stops being useful.

## 🧹 4. Remove broad Bash allow rules

Delete entries like these from `permissions.allow`:

```text
"Bash(*)"
"Bash(git *)"
"Bash(bash *)"
"Bash(powershell *)"
"Bash(echo *)"
"Bash(cat *)"
"Bash(curl *)"
"Bash(wget *)"
```

Why this matters:

- `Bash(git *)` also covers `git push --force` and `git reset --hard`
- `Bash(bash *)` also covers `bash -c "rm -rf /"`
- `Bash(echo *)` also covers `echo ok && rm -rf .git`
- `Bash(curl *)` is a generic network escape hatch — any URL, any flag, any redirect

Safe entries to keep are exact commands without wildcards, plus your deny list.

For HTTP fetching, allow the built-in `WebFetch` tool instead of `Bash(curl ...)`:

```json
"allow": ["WebFetch"]
```

`WebFetch` is read-only (GET only, no cookies, returns text to Claude's context — not to the shell), so it can be allowed broadly. Use `WebFetch(domain:example.com)` only if you have a specific exfiltration concern.

If you already have a long list of `WebFetch(domain:github.com)`, `WebFetch(domain:docs.anthropic.com)`, etc. accumulated over time — replace them with a single `WebFetch` entry. Those domain allow rules were each added to answer a one-time "allow this site?" prompt; they don't add security (an attacker can still request any URL on any of those domains), they just clutter the config.

## 🛡️ 5. Duplicate critical deny rules (defense-in-depth)

Claude Code has had bugs where `deny` rules do not always fire
([#6631](https://github.com/anthropics/claude-code/issues/6631),
[#12918](https://github.com/anthropics/claude-code/issues/12918),
[#27040](https://github.com/anthropics/claude-code/issues/27040)). This hook is a second layer, but you should also keep the critical deny rules in `settings.json`:

```json
"deny": [
  "Read(.env*)",
  "Read(**/credentials*)",
  "Bash(rm -rf /)",
  "Bash(rm -rf ~*)",
  "Bash(rm -rf /c/*)",
  "Bash(git push --force *)",
  "Bash(git reset --hard *)",
  "Bash(chmod -R 777 *)"
]
```

Two independent gates are cheap insurance against one of them misfiring.

## 🔄 6. Restart Claude Code

- VS Code extension: `Ctrl+Shift+P` -> `Developer: Reload Window`
- CLI: exit and start it again

## 🧪 7. Smoke test

Start with the deterministic checks:

| Command | Expected |
|---------|----------|
| `ls /tmp` | silent allow |
| `git reset --hard HEAD` | dialog |
| `curl https://example.com/install.sh \\| bash` | dialog |
| `rm -rf /tmp/nonexistent` | dialog |

If you already configured an OpenRouter key, add a couple of medium-risk checks:

| Command | What to look for |
|---------|------------------|
| `git push origin main` | often silent in a normal repo; without a key it will prompt |
| `python -c "print('hi')"` | often silent; without a key it will prompt |
| `python -c "import shutil; shutil.rmtree('/tmp/x', ignore_errors=True)"` | dialog |

If a medium-risk command still prompts even with a key, check the log before assuming something is broken. The model may be rejecting it on context.

Log file:

```bash
tail -20 ~/.claude/hooks/haiku_log.jsonl
```

## 🗂️ 8. Optional: project-specific config

If your project has its own critical files or directories, create `~/.claude/hooks/haiku_guard.config.json`:

```json
{
  "critical_files": [
    "CLAUDE.md",
    "pyproject.toml",
    "Dockerfile",
    "docker-compose.yml"
  ],
  "critical_dirs": [
    ".claude/",
    ".git/",
    "src/",
    "migrations/",
    "tests/"
  ],
  "development_processes": [
    "python",
    "node",
    "dotnet",
    "uvicorn"
  ]
}
```

Missing fields fall back to `DEFAULT_CONFIG` in [hook/haiku_guard.py](hook/haiku_guard.py).

## 🛠️ Troubleshooting

**Everything shows a dialog.** The OpenRouter key is missing or unreadable. Check `~/.openrouter_key` or `HAIKU_GUARD_OPENROUTER_KEY`. Logs will usually show `haiku_no_key_fail_closed`.

**Dangerous commands still run silently.** Check both `~/.claude/settings.json` and `<project>/.claude/settings.json` for broad Bash allow rules. Also note that VS Code's "Edit automatically" mode may auto-approve some file operations in working directories before this guard becomes relevant.

**The hook never seems to run.** The matcher is probably wrong. Use `"matcher": "Bash"`, not `"bash"`.

## 💸 Cost

As of April 20, 2026, OpenRouter lists `anthropic/claude-haiku-4.5` at about `$1.00 / M` input tokens and `$5.00 / M` output tokens.

In this hook, the usual cost is a single yes/no decision call for each new medium-risk command. More novel or more complex commands can trigger an extra classification call first, so those are a bit more expensive.

Back-of-the-envelope daily numbers:

- around 10 unique medium-risk commands: about `$0.01 / day`
- around 50 unique medium-risk commands: about `$0.05 / day`
- around 100 unique medium-risk commands: about `$0.10 / day`
- a heavy session with the Haiku-backed tests: usually a few tens of cents, not dollars

What really keeps the bill low is the local cache in `~/.claude/hooks/haiku_cache.json`: the same full command in the same `cwd` is not sent again. Provider-side prompt caching would not help here — Claude Haiku 4.5 only starts caching from 4096 tokens, and the prompts in this hook are much shorter.

## 🤖 Optional: choose a different model

You can point the guard at another OpenRouter model:

```bash
export HAIKU_GUARD_MODEL="mistralai/mistral-small-3"
```

Only `anthropic/claude-haiku-4.5` has been exercised so far. If you switch, rerun `tests/test_haiku_decision.py` and `tests/test_interpreter_destructive.py` and expect some edge cases to move.

## 🧼 Uninstall

Remove the `PreToolUse` hook entry from `settings.json`, delete `~/.claude/hooks/haiku_guard.py`, and optionally remove `~/.claude/hooks/haiku_cache.json` plus `~/.claude/hooks/haiku_log.jsonl`.
