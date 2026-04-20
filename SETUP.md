# Setup

Step-by-step walkthrough for wiring `haiku_guard.py` into Claude Code.

## 1. Get an OpenRouter API key

The guard uses [OpenRouter](https://openrouter.ai/) to call Claude Haiku cheaply. An OpenAI/Anthropic key will **not** work — the code targets OpenRouter's endpoint.

1. Go to <https://openrouter.ai/settings/keys>
2. Create a key. It starts with `sk-or-...`
3. Put it in `~/.openrouter_key` (one line, nothing else):

```bash
echo "sk-or-v1-..." > ~/.openrouter_key
chmod 600 ~/.openrouter_key  # Linux/macOS
```

On Windows (Git Bash): the `~` expands to `C:\Users\<you>`, so the file lands at `C:\Users\<you>\.openrouter_key`.

Alternative: set `HAIKU_GUARD_OPENROUTER_KEY` env var directly. Claude Code's own `settings.json` has an `env` block for this.

## 2. Install the hook file

```bash
mkdir -p ~/.claude/hooks
cp hook/haiku_guard.py ~/.claude/hooks/haiku_guard.py
```

Verify Python can run it standalone:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls /tmp"}}' \
  | python ~/.claude/hooks/haiku_guard.py
```

Expected output (single JSON line):

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"auto: none: read-only"}}
```

If you see a decision payload, the hook is working. If you get a Python error, your Python is likely < 3.9 — this code uses `datetime.timezone.utc`.

## 3. Wire the hook into `settings.json`

The guard should run as a `PreToolUse` hook matched on `Bash`. Open `~/.claude/settings.json` and merge the `hooks` block from [examples/settings.json](examples/settings.json):

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

Why `PreToolUse` and not `PermissionRequest`: `PreToolUse` fires **before** Claude Code's own allow-list check, so the guard sees every Bash command. `PermissionRequest` only fires once Claude Code has already decided to show a dialog, which is too late.

The hook also supports `PermissionRequest` — if you already have one, you can point it at the same script for non-Bash tools.

## 4. Prune your allow-list

**This is the whole point.** Remove any `Bash(<something> *)` entries from `permissions.allow`. Each one is a potential bypass because glob `*` matches spaces, `&&`, `;`, and `|`.

Bad examples to delete:
```
"Bash(*)",            ❌ trivially lets everything through
"Bash(git *)",        ❌ `git push --force`, `git reset --hard` silently allowed
"Bash(bash *)",       ❌ `bash -c "rm -rf /"` silently allowed
"Bash(powershell *)", ❌ arbitrary PowerShell silently allowed
"Bash(echo *)",       ❌ `echo ok && rm -rf .git` silently allowed
"Bash(cat *)",        ❌ `cat x || git reset --hard` silently allowed
```

Safe to keep: entries that pin **exact commands without wildcards** and the `deny` list.

## 5. Restart Claude Code

VS Code extension: `Ctrl+Shift+P` → `Developer: Reload Window`.
CLI: exit and restart.

## 6. Smoke test

Run these one by one and check the behavior matches:

| Command | Expected |
|---------|----------|
| `ls /tmp` | silent (none) |
| `python -c "print('hi')"` | silent (Haiku says safe) |
| `python -c "import shutil; shutil.rmtree('/tmp/x', ignore_errors=True)"` | **dialog** (Haiku spots destructive content) |
| `git push origin main` | silent (Haiku says routine) |
| `git reset --hard HEAD` | **dialog** (rule says high) |
| `rm -rf /tmp/nonexistent` | **dialog** (rule says high — note it's classified by pattern, not outcome) |

Check the log for what happened:

```bash
tail -20 ~/.claude/hooks/haiku_log.jsonl
```

## 7. Optional: project-specific config

If your project has its own critical artefacts, create `~/.claude/hooks/haiku_guard.config.json`:

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

The Haiku decision prompt will refuse to silently delete/move anything matching these lists — it will surface a dialog instead. Fall through to the defaults in `DEFAULT_CONFIG` (see the top of [hook/haiku_guard.py](hook/haiku_guard.py)) if you skip this file.

## Troubleshooting

**Dialogs appear on everything, even safe commands.** Your OpenRouter key isn't readable. Check `cat ~/.openrouter_key` — it should print a single `sk-or-...` line. Logs will show `haiku_no_key_fail_closed`.

**Dialogs don't appear even on `rm -rf`.** Allow-list is still too broad. Check both `~/.claude/settings.json` and `<project>/.claude/settings.json` — the hook runs *after* the allow-list. Also verify your VS Code mode — "Edit automatically" auto-approves some file ops (`mv`, `rm`, `cp`) in working directories before the hook sees them.

**Hook doesn't log anything.** The `PreToolUse` matcher didn't match. Common mistake: using `"matcher": "bash"` (lowercase). The tool name is `Bash` with a capital B.

**"fatal: not a git repository" during classification.** The hook calls `os.getcwd()`; if Claude Code runs it in a dir without a `.git`, that's fine for the hook but logs may look confused. Irrelevant to correctness.

## Choosing a model

The default model is `anthropic/claude-haiku-4.5`. The guard is designed around its strengths: fast, cheap, strong at instruction-following, and trained on Anthropic's safety intuitions.

### Alternative models

You can swap in any OpenRouter-hosted model via `HAIKU_GUARD_MODEL`:

```bash
export HAIKU_GUARD_MODEL="mistralai/mistral-small-3"
```

**Candidates for the yes/no decision task** (not benchmarked yet — see disclaimer below):

| Model | Price in / out ($/M) | Notes |
|-------|----------------------|-------|
| `anthropic/claude-haiku-4.5`       | $0.80 / $4.00 | Default. Best safety intuitions. |
| `openai/gpt-4o-mini` / `gpt-5-mini` | ~ $0.15 / $0.60 | Strong instruction-following. |
| `google/gemini-2.5-flash`          | ~ $0.30 / $2.50 | Very fast, big context. |
| `mistralai/mistral-small-3`        | ~ $0.10 / $0.30 | Cheapest credible option. |
| `deepseek/deepseek-v3.2-exp`       | ~ $0.30 / $1.20 | Strong reasoning on nuanced cases. |
| `qwen/qwen3-next-80b-a3b-instruct` | ~ $0.15 / $1.50 | MoE, fast, broad training. |
| `meta-llama/llama-3.3-70b-instruct`| ~ $0.50 / $0.50 | Stable, slightly weaker on edges. |

### Disclaimer — no comparative testing yet

This table lists **candidate** alternatives. As of this writing the guard has only been validated against `anthropic/claude-haiku-4.5`. Other models are plausible substitutes — especially for the bounded yes/no task — but may differ on:

- false-positive rate on edge cases (e.g. `python -c` with mixed read/write)
- prompt-injection resistance inside command bodies
- consistency between runs

If you switch, run `tests/test_haiku_decision.py` and `tests/test_interpreter_destructive.py` first to calibrate against your chosen model, and expect a few cases to flip — tune the prompt in `_build_decision_prompt()` if needed.

Regional note: OpenRouter blocks OpenAI / Anthropic / Google models for some billing regions. If that happens, either change your billing region in OpenRouter settings, or pick one of the non-blocked models (Mistral, DeepSeek, Qwen, Llama).

## Cost estimation

Each `medium`-level command triggers one Haiku call. Token breakdown per call:

- system prompt (cached): ~600 tokens
- user message (command + cwd + description): ~100 tokens
- output: 1-5 tokens ("yes"/"no")

**Prompt caching is enabled** — the system prompt is sent with
`cache_control: {"type": "ephemeral"}`, so Anthropic caches it on their side
with a 5-minute TTL. Back-to-back requests within the window pay ~10% of the
system-prompt input cost instead of full price.

Without caching: ~$0.0006 per call → ~$0.05 for 100 calls / day.
With caching (typical active-session hit rate): ~2-5× cheaper.

Heavy testing across all suites consumes several hundred calls in minutes —
expect $0.30-0.50 per full-suite run. Normal dev work is an order of magnitude
less. Cheaper substitute models (Mistral Small, Qwen) cut the floor by another
5-10× if absolute cost matters more than model quality.

## Uninstall

Remove the `PreToolUse` hook entry from `settings.json`, delete `~/.claude/hooks/haiku_guard.py`, optionally delete `~/.claude/hooks/haiku_cache.json` and `~/.claude/hooks/haiku_log.jsonl`.
