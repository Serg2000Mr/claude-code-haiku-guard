# 🛡️ claude-code-haiku-guard

Anyone who uses Claude Code heavily eventually gets tired of approving harmless commands. Most people start by adding allow-list exceptions, and some end up switching on `--dangerously-skip-permissions`. The incident reports linked below are a good reminder of why that trade-off is risky.

The idea in this repo is simple: keep rules for obviously safe commands, and use a small, fast model such as Haiku for the harder cases. In practice, that means most routine commands are approved automatically, while the user only sees the commands that look dangerous or unclear.

Technically, this is a Claude Code hook that classifies the full Bash command by risk. By default, the LLM step runs through OpenRouter.

> [Русская версия →](README.ru.md)

## ⚖️ What happens to a command

| Risk | Behavior |
|------|----------|
| `none` / `low` | allow silently |
| `medium` | ask Haiku for a yes/no decision in context |
| `high` / `critical` | always show a dialog |

Public incident examples that motivated these defaults are collected in [INCIDENTS.md](INCIDENTS.md).

## 🔍 How it decides

1. Match known command shapes with regex rules.
2. Keep interpreter wrappers such as `python -c`, `bash -c`, and `powershell -Command` at least `medium`.
3. Use Haiku for medium-risk or novel cases, with the command, current working directory, and optional project-specific config.
4. Cache decisions by full command plus `cwd`.

If the OpenRouter key is missing, the network fails, or the model call errors out, medium-risk commands fall back to a dialog instead of silent allow. High and critical commands never bypass the dialog.

## 🚀 Quick start

See [SETUP.md](SETUP.md) for the full walkthrough. The short version is:

1. Copy `hook/haiku_guard.py` to `~/.claude/hooks/haiku_guard.py`.
2. Put your OpenRouter key (`sk-or-...`) in `~/.openrouter_key`.
3. Merge the `PreToolUse` hook from [examples/settings.json](examples/settings.json) into `~/.claude/settings.json`.
4. Remove broad `Bash(<something> *)` entries from your allow-list.
5. Restart Claude Code.

## ⚙️ Configuration

Optional config lives at `~/.claude/hooks/haiku_guard.config.json`:

```json
{
  "critical_files": ["CLAUDE.md", "*.csproj", "MyProject.sln"],
  "critical_dirs": [".claude/", "src/core/", "migrations/"],
  "development_processes": ["dotnet", "node", "python"]
}
```

Missing fields fall back to `DEFAULT_CONFIG` in [hook/haiku_guard.py](hook/haiku_guard.py).

Environment variables:

- `HAIKU_GUARD_OPENROUTER_KEY`
- `HAIKU_GUARD_OPENROUTER_KEY_FILE`
- `HAIKU_GUARD_MODEL`
- `HAIKU_GUARD_TIMEOUT`

The default model is `anthropic/claude-haiku-4.5`. You can point `HAIKU_GUARD_MODEL` at another OpenRouter model, but only the default has been exercised so far. If you switch models, rerun the Haiku-backed tests first.

## 🧪 Tests

```bash
cd tests
python test_classifier.py
python test_haiku_decision.py
python test_interpreter_destructive.py
```

`test_classifier.py` is fully offline. The two Haiku-backed suites require an OpenRouter key and skip cleanly when no key is configured.

## ⚠️ Limitations

- The rules and examples are tuned for Windows and Git Bash.
- The hook contract is specific to Claude Code.
- Medium-risk decisions depend on OpenRouter. Without a key, they fall back to a dialog.
- Broad Bash allow rules in Claude settings still defeat the point of this guard, so keep them out of both global and project settings.
- Cache entries are scoped to the full command and `cwd`, so the same command in another directory is evaluated again.

## License

MIT
