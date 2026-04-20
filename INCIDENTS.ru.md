# Реальные инциденты с AI-агентами для разработки

**AI-агенты действительно уничтожают работу пользователей. Не через экзотические jailbreak — через обычные админские команды, которые проскочили мимо подтверждения.** Инциденты ниже публично задокументированы в Claude Code, Cursor и Codex за последний год. Паттерны повторяются: рекурсивное удаление, деструктивный git, shell-обёртки с произвольным кодом, убийство процессов.

> [English version →](INCIDENTS.md)

## Обычные подозреваемые

Пять классов команд отвечают почти за все отчёты об инцидентах:

- `rm -rf` (и `Remove-Item -Recurse -Force` на Windows)
- `git reset --hard` / `git checkout -- .` / `git restore` / `git clean`
- shell- и interpreter-обёртки с произвольным кодом: `bash -c`, `python -c`, `powershell -Command`
- убийство процессов: `kill`, `pkill`, `killall`, `taskkill`
- массовые деструктивные циклы в PowerShell: `Clear-RecycleBin -Force`, вложенные `Remove-Item`

Общий механизм провала один: allow-правило сматчило «безобидно выглядящий» префикс, реальный guard не запустился, и деструктивный хвост команды выполнился молча.

## Публичные отчёты

### Claude Code

- **2025-08-26** — `rm -rf` выполнен без подтверждения.
  <https://github.com/anthropics/claude-code/issues/6608>
- **2025-09-06** — `git reset --hard cff6f72`, затем `git checkout -- .`. Незакоммиченная работа потеряна.
  <https://github.com/anthropics/claude-code/issues/7232>
- **2025-10-21** — удаление домашнего каталога, сценарий совпадает с `rm -rf` от корня. Точная команда в логе не сохранена, evidence указывает на этот класс.
  <https://github.com/anthropics/claude-code/issues/10077>

### Codex

- **2025-12-31** — `git restore` выполнен вопреки явной инструкции «не трогай git».
  <https://github.com/openai/codex/issues/8643>
- **2026-02-19** — массовое удаление файлов; в evidence фигурируют `Clear-RecycleBin -Force` и вложенные циклы `Remove-Item`.
  <https://github.com/openai/codex/issues/12277>

### Cursor

- **2025-12-08** — `rm -rf` по git-tracked каталогам, плюс `pkill`.
  <https://forum.cursor.com/t/catastrophic-damage-and-chaos-in-plan-mode/145523>
- **2026-03-14** — подтверждённый staff-ом баг: удаления вне workspace без подтверждения.
  <https://forum.cursor.com/t/agents-deleting-files-outside-workspace-without-confirmation/154768>
- **2026-03-26** — подтверждённый staff-ом баг: allow-list не применяется в sandbox auto-run так, как ожидает пользователь.
  <https://forum.cursor.com/t/agent-did-remove-file-without-confirmation/155925>

## Зачем этот хук

Поверхность отказа предсказуема, и список опасных паттернов небольшой. Guard не обязан быть умным — он обязан гарантировать, что **ни одно allow-правило не может обойти классификацию**, и что деструктивные шаблоны команд всегда попадают на подтверждение пользователю, даже если конкретные аргументы делают команду no-op.

Именно это реализовано в этом репозитории.

## Оговорка

Публичные issue-треды — не всегда полностью расследованные post-mortem. Но как основа для defensive policy они полезны: одни и те же классы команд всплывают снова и снова, независимо от агента и IDE. Если guard их не блокирует, пользователи рано или поздно на них попадут.
