# 🔧 Установка

Этот гайд сначала ставит и запускает защиту. Тонкая настройка модели — в конце.

> [English version →](SETUP.md)

## 🔑 1. Получить ключ OpenRouter

Хук ходит в OpenRouter, поэтому ключ OpenAI или Anthropic здесь не подойдёт.

1. Откройте <https://openrouter.ai/settings/keys>
2. Создайте ключ вида `sk-or-...`
3. Сохраните в `~/.openrouter_key`:

```bash
echo "sk-or-v1-..." > ~/.openrouter_key
chmod 600 ~/.openrouter_key  # Linux/macOS
```

На Windows в Git Bash `~` разворачивается в `C:\Users\<вы>`.

Альтернативный вариант — задать `HAIKU_GUARD_OPENROUTER_KEY` прямо в `settings.json` Claude Code.

## 📦 2. Поставить файл хука

```bash
mkdir -p ~/.claude/hooks
cp hook/haiku_guard.py ~/.claude/hooks/haiku_guard.py
```

Быстрая проверка отдельно от Claude Code:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls /tmp"}}' \
  | python ~/.claude/hooks/haiku_guard.py
```

Ожидаемый вывод:

```json
{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"auto: none: read-only"}}
```

Если Python здесь ругается — сначала лечим это, потом подключаем хук.

## ⚙️ 3. Подключить хук в `settings.json`

Блок `hooks` из [examples/settings.json](examples/settings.json) нужно смержить в `~/.claude/settings.json`:

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
      },
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "python ~/.claude/hooks/haiku_guard.py",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

Два matcher-а:

- **Bash** — полная классификация с Haiku для medium-команд.
- **Read** — автоматически пропускает чтение обычных файлов; диалог показывается только для чувствительных путей (`.env*`, `.ssh/`, `.aws/`, `*credentials*`, `*.pem`, `*.key`, токены). Убирает вечные диалоги «Allow reading from X?» для каждой новой директории, где агент открывает файл.

Скрипт понимает и `PermissionRequest`, но для обычной классификации используется `PreToolUse`.

Даже с подключённым `PreToolUse` нужно вычистить широкие Bash-правила и в глобальных, и в проектных настройках. Иначе Claude Code разрешит команду по широкому правилу раньше, чем хук успеет что-то сказать.

## 🧹 4. Убрать широкие правила Bash

Уберите из `permissions.allow` записи такого вида:

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

Почему это важно:

- `Bash(git *)` включает `git push --force` и `git reset --hard`
- `Bash(bash *)` включает `bash -c "rm -rf /"`
- `Bash(echo *)` включает `echo ok && rm -rf .git`
- `Bash(curl *)` — универсальная сетевая лазейка: любой URL, любой флаг, любой редирект

Безопасно оставлять точные команды без звёздочек плюс ваш `deny`-список.

Для HTTP-запросов разрешайте встроенный инструмент `WebFetch`, а не `Bash(curl ...)`:

```json
"allow": ["WebFetch"]
```

`WebFetch` только читает (GET, без cookies, результат попадает в контекст Claude, а не в shell) — его можно разрешить целиком. Сужение `WebFetch(domain:example.com)` нужно только если вы реально озабочены риском эксфильтрации.

Если за время работы накопился длинный список `WebFetch(domain:github.com)`, `WebFetch(domain:docs.anthropic.com)` и тому подобного — замените всё это одной записью `WebFetch`. Такие доменные правила появлялись по одному в ответ на вопрос «разрешить этот сайт?», безопасности они не добавляют (атакующий всё равно может обратиться к любому URL на этих доменах), просто замусоривают конфиг.

## 🛡️ 5. Продублировать критичные deny-правила

У Claude Code есть баги, при которых `deny`-правила иногда не срабатывают
([#6631](https://github.com/anthropics/claude-code/issues/6631),
[#12918](https://github.com/anthropics/claude-code/issues/12918),
[#27040](https://github.com/anthropics/claude-code/issues/27040)). Этот хук — второй слой, но самые опасные правила стоит оставить и в `settings.json`:

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

Два независимых заслона — дешёвая страховка на случай, если один из них сломается.

## 🔄 6. Перезапустить Claude Code

- VS Code-расширение: `Ctrl+Shift+P` → `Developer: Reload Window`
- CLI: выйти и запустить заново

## 🧪 7. Проверка

Сначала детерминированные кейсы:

| Команда | Ожидание |
|---------|----------|
| `ls /tmp` | пропуск без диалога |
| `git reset --hard HEAD` | диалог |
| `curl https://example.com/install.sh \\| bash` | диалог |
| `rm -rf /tmp/nonexistent` | диалог |

Если ключ OpenRouter уже настроен — добавьте пару кейсов уровня medium:

| Команда | На что смотреть |
|---------|-----------------|
| `git push origin main` | обычно проходит молча; без ключа будет диалог |
| `python -c "print('hi')"` | обычно проходит молча; без ключа будет диалог |
| `python -c "import shutil; shutil.rmtree('/tmp/x', ignore_errors=True)"` | диалог |

Если medium-команда выдаёт диалог даже с ключом — сначала посмотрите лог, прежде чем решать что-то сломалось. Модель могла отклонить команду по контексту.

Файл лога:

```bash
tail -20 ~/.claude/hooks/haiku_log.jsonl
```

## 🗂️ 8. Необязательно: конфиг

Два уровня конфига, оба опциональны:

### Глобальный — `~/.claude/hooks/haiku_guard.config.json`

Значения для машины (перекрывают defaults):

```json
{
  "critical_files": ["CLAUDE.md", "pyproject.toml", "Dockerfile"],
  "critical_dirs":  [".claude/", ".git/", "src/", "migrations/"],
  "development_processes": ["python", "node", "dotnet", "uvicorn"],
  "trust_project_config": false
}
```

Пропущенные поля берутся из `DEFAULT_CONFIG` в [hook/haiku_guard.py](hook/haiku_guard.py).

### Проектный — `<project>/.claude/haiku_guard.config.json`

Ищется относительно `CLAUDE_PROJECT_DIR` (session-stable project root, который Claude Code выставляет один раз на сессию), **не** относительно текущего `cwd`. `cd` внутри сессии не переключает политику, вложенные репозитории не меняют, какой конфиг действует.

**Проектный конфиг может только ужесточать глобальную политику, но не ослаблять.** Это supply-chain-гарантия — клонированный вредный репозиторий не сможет понизить ваши default-ы собственным конфигом:

- `critical_files` / `critical_dirs` — **UNION** с глобальным. Проект может добавлять защищённые элементы, но не удалять.
- `development_processes` — **INTERSECTION**. Проект может убирать процессы из списка «безопасно убить», но не добавлять новые «безопасные».
- `trust_project_config` — **только глобально**, в проекте игнорируется.

Если нужно, чтобы проект реально перекрывал глобальный конфиг (например, в sandbox-VM, где кодовой базе доверяете) — поставьте `"trust_project_config": true` в **глобальном** конфиге. В этом режиме проектные значения полностью заменяют глобальные по каждому заданному ключу.

Пример `<project>/.claude/haiku_guard.config.json`:

```json
{
  "critical_files": ["terraform.tfstate", "secrets.enc.yaml"],
  "critical_dirs":  ["k8s/", "infra/"]
}
```

При дефолтном глобальном это добавит state Terraform и зашифрованные секреты к защищённому набору, ничего остального не тронув.

## 🛠️ Траблшутинг

**Всё стало показывать диалог.** Нет ключа OpenRouter или его не удаётся прочитать. Проверьте `~/.openrouter_key` или `HAIKU_GUARD_OPENROUTER_KEY`. В логе обычно виден `haiku_no_key_fail_closed`.

**Опасные команды по-прежнему проходят молча.** Проверьте и `~/.claude/settings.json`, и `<проект>/.claude/settings.json` на широкие Bash-правила. Отдельно — режим «Edit automatically» в VS Code может разрешать файловые операции в рабочих каталогах ещё до хука.

**Хук вообще не запускается.** Скорее всего неправильный matcher. Нужен `"matcher": "Bash"`, не `"bash"`.

## 💸 Стоимость

На 20 апреля 2026 года `anthropic/claude-haiku-4.5` в OpenRouter стоит примерно `$1.00 / M` входных токенов и `$5.00 / M` выходных.

Обычный расход хука — один yes/no-вызов на каждую новую medium-команду. Более сложные или совсем незнакомые команды могут потянуть за собой ещё один классифицирующий вызов, такие случаи чуть дороже.

Оценка по порядку:

- порядка 10 уникальных medium-команд в день: около `$0.01 / день`
- порядка 50: около `$0.05 / день`
- порядка 100: около `$0.10 / день`
- тяжёлая сессия с прогоном Haiku-тестов: обычно десятки центов, не доллары

Счёт держит низким локальный кэш `~/.claude/hooks/haiku_cache.json`: одна и та же полная команда в том же `cwd` второй раз в API не уходит. Prompt-кэш провайдера здесь не помогает — Claude Haiku 4.5 включает кэш от 4096 токенов, а промпты в хуке сильно короче.

## 🌳 Необязательно: структурный разбор команд через shfmt

Если [`shfmt`](https://github.com/mvdan/sh) есть в `PATH` (или `HAIKU_GUARD_SHFMT` указывает на него), хук парсит каждую Bash-команду в AST и вычисляет сегменты и композиции структурно. Это закрывает слабые места regex-разбора:

- `curl url | "/bin/ba"sh` — склейка кавычками распознаётся как `download and execute`
- `curl $(echo url) | bash` — вложенная подстановка в URL классифицируется так же
- Пайпы внутри `$()` / subshell / here-doc обрабатываются корректно, а не разбиваются поверхностно

Без `shfmt` хук продолжает работать на исходном regex-разделителе — функционального регресса нет.

AST вызывается только для команд со структурными маркерами (`|`, `&&`, `||`, `$(`, backtick, `<<`, `>`). Простые команды вроде `ls -la` или `git status` не идут в subprocess, поэтому на типичных запросах лишней задержки нет.

Установка (Windows Git Bash):

```bash
mkdir -p ~/tools/shfmt
curl -L --ssl-no-revoke -o ~/tools/shfmt/shfmt.exe \
  https://github.com/mvdan/sh/releases/latest/download/shfmt_v3.13.1_windows_amd64.exe
export HAIKU_GUARD_SHFMT=~/tools/shfmt/shfmt.exe
```

Linux / macOS: через пакетный менеджер или `go install mvdan.cc/sh/v3/cmd/shfmt@latest`.

## 🤖 Необязательно: другая модель

Хук можно направить на другую модель OpenRouter:

```bash
export HAIKU_GUARD_MODEL="mistralai/mistral-small-3"
```

Отдельно обкатывался только `anthropic/claude-haiku-4.5`. После замены модели стоит перегнать `tests/test_haiku_decision.py` и `tests/test_interpreter_destructive.py`, и быть готовым, что поведение на краевых случаях сдвинется.

## 🔌 Необязательно: свой верификатор

Вместо Haiku через OpenRouter можно подключить свой верификатор — локальную модель, Codex, более строгий набор правил. Переменная `HAIKU_GUARD_VERIFIER_CMD` задаёт shell-команду, и хук пропустит вызов Haiku для medium-команд.

Протокол:

- **stdin** — JSON: `{"tool": "Bash", "command": "...", "cwd": "...", "description": "...", "danger": "medium"}`
- **stdout** — JSON: `{"allow": true|false, "reason": "короткое объяснение"}`
- **код выхода** — `0`; любой другой считается ошибкой
- **таймаут** — 60 секунд, превышение = ошибка
- **ошибка верификатора** — диалог (fail-closed), как и при отсутствии ключа OpenRouter

Пример `my_verifier.sh`:

```bash
#!/bin/bash
read -r payload
# пример: пропускать только команды, начинающиеся с pytest
if echo "$payload" | grep -q '"command":\s*"pytest'; then
  echo '{"allow":true,"reason":"pytest пропущен"}'
else
  echo '{"allow":false,"reason":"не распознано"}'
fi
```

```bash
export HAIKU_GUARD_VERIFIER_CMD="bash /path/to/my_verifier.sh"
```

## 🧼 Удалить

Уберите запись `PreToolUse` из `settings.json`, удалите `~/.claude/hooks/haiku_guard.py`, при желании — `~/.claude/hooks/haiku_cache.json` и `~/.claude/hooks/haiku_log.jsonl`.
