# Changelog

All notable user-visible changes live here. For the full commit history see `git log`.

## 2026-04-22

### Optional shfmt AST backend
- When `shfmt` is on `PATH` (or `HAIKU_GUARD_SHFMT` points to it), compound commands are parsed into an AST before classification. Segments, composition detection and `download | interpreter` patterns are derived from the AST instead of a flat regex split.
- Catches obfuscation the regex missed: `curl url | "/bin/ba"sh` (quote-concat), `curl $(echo url) | bash` (nested substitution), pipes inside `$()` / subshells / here-docs.
- Gated by structural markers (`|`, `&&`, `||`, `$(`, backtick, `<<`, `>`) — simple commands like `ls -la` or `git status` skip the subprocess entirely; no added latency on the common path.
- AST backend is optional; without `shfmt` the hook falls back to regex split with no functional regression.

### Read tool coverage
- New `Read` matcher in the default `settings.json` — the hook now auto-allows reads of normal files and surfaces a dialog only for sensitive paths.
- Sensitive-path patterns: `.env*`, `.ssh/`, `.aws/`, `.gnupg/`, `*credentials*`, `*secrets*`, `*.pem`, `*.key`, `*.pfx`, `*.p12`, `*_token*`, `id_rsa`/`id_ed25519`, `.netrc`.
- Removes the recurring "Allow reading from X?" prompts that Claude Code shows for every new directory.

## 2026-04-21

### Catastrophic command intercept
- `rm -rf /`, `rm -rf ~`, `dd of=/dev/sd*`, `mkfs /dev/sd*`, fork bomb, `chmod -R 777 /` are intercepted before they reach the user dialog.
- The dangerous command is replaced with a harmless `echo` via the `updatedInput` field, then surfaced as `ask`. Even an accidental Yes click cannot cause damage.
- A modal MessageBox (error icon, SYSTEMMODAL) appears: the agent cannot continue until the user acknowledges.
- `additionalContext` injects a strong stop-directive into the agent's context: "stop, acknowledge to user, wait for explicit instructions, do not attempt workarounds".
- `permissionDecisionReason` tells the user what was replaced and why.

### New
- **Reason exposed in the permission decision** — Haiku now returns `{"verdict":"yes|no","reason":"..."}`. The reason appears in `permissionDecisionReason` that Claude Code logs, and in the local `haiku_log.jsonl`. Makes false positives much easier to debug.
- **Custom verifier hook** — set `HAIKU_GUARD_VERIFIER_CMD` to a shell command that receives `{command, cwd, danger, description}` on stdin and returns `{"allow": bool, "reason": "..."}` on stdout. Lets you swap Haiku for your own model, a stricter ruleset, Codex, etc. See SETUP.md.
- `node --check`, `node --version`, `node -v` auto-allowed as read-only
- `ForEach-Object` added to the PowerShell read-only pipeline list in the Haiku prompt

### Fewer false positives
- `git push`, `git pull`, `git fetch` are now classified as `low` — no dialog on a normal push/pull
- `python3 test_*.py`, `python3 -m pytest`, `dotnet test` — auto-allowed (`none`)
- `echo "" > path` no longer triggers the write-redirect bump (treated as `touch`-equivalent)
- `git commit -m "$(cat <<'EOF'...EOF)"` heredoc bodies are stripped before analysis — standard git workflows stop going through the LLM
- Length-based complexity check removed from `is_complex` — only real markers (`$(`, backticks, `<<`, `bash -c`, etc.) trigger the LLM path
- Haiku decision prompt: explicitly allow reading config files (`open(path, 'r')`, `json.load`, `cat`) on any path, including `.claude/settings.json`

### Docs
- `SETUP.ru.md` added (full Russian setup guide)
- New section: duplicate critical `deny` rules in `settings.json` as defense-in-depth (Claude Code bugs [#6631](https://github.com/anthropics/claude-code/issues/6631), [#12918](https://github.com/anthropics/claude-code/issues/12918), [#27040](https://github.com/anthropics/claude-code/issues/27040) — deny rules occasionally fail to fire)
- New advice: use `WebFetch` (without `domain:`) instead of `Bash(curl *)`; it is GET-only, no cookies, no shell exposure
- New advice: consolidate accumulated `WebFetch(domain:*)` entries into a single `WebFetch`
- `cache_control` note in SETUP updated — the field was removed from the API call because Haiku 4.5 prompt caching only starts at 4096 tokens and prompts here are shorter

## 2026-04-20 — Initial public release

- PreToolUse Bash hook with five-level risk classification (`none` / `low` / `medium` / `high` / `critical`)
- Haiku decision layer for `medium` commands, with command + cwd + project config as context
- Interpreter floor — `python -c`, `bash -c`, `powershell -Command`, `node -e` never fall below `medium`
- Composition pattern detection — `curl | bash`, `wget | sh` are escalated to `high`
- Write-redirect detection — `> file` or `>> file` bumps `none`/`low` to `medium`
- Local decision cache keyed by `(cmd, cwd)` in `~/.claude/hooks/haiku_cache.json`
- Fail-closed design: missing key, network failure, or API error → dialog (never silent allow)
- Windows MessageBox notification when the OpenRouter key is missing, expired, or out of credits
- Project-specific config via `~/.claude/hooks/haiku_guard.config.json` (`critical_files`, `critical_dirs`, `development_processes`)
- English + Russian README and INCIDENTS docs
- Offline classifier tests (52 cases) and optional Haiku-backed integration tests
