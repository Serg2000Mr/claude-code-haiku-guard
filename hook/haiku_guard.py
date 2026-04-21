"""Claude Code Bash permission guard powered by Claude Haiku.

Flow:
  1. Hook reads tool_input from stdin (PreToolUse or PermissionRequest).
  2. For Bash, classify the command by regex rules with LLM fallback.
  3. Classification is floored for interpreter wrappers (python -c, bash -c, ...)
     so LLM cannot downgrade container commands below "medium".
  4. Decision:
       - none / low  -> auto allow (silent)
       - medium      -> ask Haiku LLM; allow if safe, else surface dialog
       - high / crit -> always dialog (Haiku cannot override)
  5. Fail-closed: missing API key or network error -> dialog, not silent allow.

Files:
  ~/.openrouter_key               Bearer token for OpenRouter (or set
                                  HAIKU_GUARD_OPENROUTER_KEY env var)
  ~/.claude/hooks/haiku_cache.json Decision cache (key = cmd + cwd)
  ~/.claude/hooks/haiku_log.jsonl  Decision log

Optional config JSON at ~/.claude/hooks/haiku_guard.config.json overrides
the "critical artifacts" and "development processes" lists used by the
Haiku decision prompt. See README.
"""
import sys
import json
import os
import re
import hashlib
import datetime
import urllib.request
import urllib.error


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_KEY_CANDIDATES = [
    os.environ.get("HAIKU_GUARD_OPENROUTER_KEY_FILE", ""),
    os.path.expanduser("~/.openrouter_key"),
]
LLM_MODEL = os.environ.get("HAIKU_GUARD_MODEL", "anthropic/claude-haiku-4.5")
LLM_TIMEOUT_SEC = int(os.environ.get("HAIKU_GUARD_TIMEOUT", "15"))
CACHE_FILE = os.path.expanduser("~/.claude/hooks/haiku_cache.json")
LOG_FILE = os.path.expanduser("~/.claude/hooks/haiku_log.jsonl")
CONFIG_FILE = os.path.expanduser("~/.claude/hooks/haiku_guard.config.json")
NOTIFY_LOCK = os.path.expanduser("~/.claude/hooks/haiku_notify.lock")
_NOTIFY_COOLDOWN_SEC = 1800  # show at most once per 30 minutes

DEFAULT_CONFIG = {
    # Files/dirs that must NEVER be silently mutated by the agent.
    "critical_files": [
        "CLAUDE.md", "settings.json", "settings.local.json",
        "*.csproj", "*.sln", "package.json", "pyproject.toml",
        "Cargo.toml", "go.mod", "Makefile", "Dockerfile",
    ],
    "critical_dirs": [
        ".claude/", ".git/", ".github/", "src/", "lib/", "app/",
    ],
    # Process names considered "development" (safe to kill).
    "development_processes": [
        "dotnet", "node", "python", "code", "ruby", "java",
    ],
}


def _load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = {**DEFAULT_CONFIG}
        out.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
        return out
    except Exception:
        return DEFAULT_CONFIG


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def log_event(event: dict) -> None:
    try:
        event = {**event, "ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Windows notification
# -----------------------------------------------------------------------------

def _notify(title: str, body: str, icon: str = "info") -> None:
    """Show a Windows MessageBox. Rate-limited to once per 30 minutes.
    icon: "info" or "error". No-op on non-Windows or when ctypes is unavailable."""
    try:
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
        if os.path.exists(NOTIFY_LOCK):
            try:
                with open(NOTIFY_LOCK, "r", encoding="utf-8") as f:
                    if now - json.load(f).get("ts", 0) < _NOTIFY_COOLDOWN_SEC:
                        return
            except Exception:
                pass
        os.makedirs(os.path.dirname(NOTIFY_LOCK), exist_ok=True)
        with open(NOTIFY_LOCK, "w", encoding="utf-8") as f:
            json.dump({"ts": now}, f)
    except Exception:
        pass
    try:
        import ctypes
        icon_flag = 0x10 if icon == "error" else 0x40  # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(
            0, body, title,
            icon_flag | 0x1000,  # icon | MB_SYSTEMMODAL
        )
    except Exception:
        pass


# -----------------------------------------------------------------------------
# OpenRouter key
# -----------------------------------------------------------------------------

def _read_first_line(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readline().strip()
    except Exception:
        return ""


def read_openrouter_key() -> str:
    env_key = os.environ.get("HAIKU_GUARD_OPENROUTER_KEY", "").strip()
    if env_key.startswith("sk-or-"):
        return env_key
    for p in OPENROUTER_KEY_CANDIDATES:
        if not p:
            continue
        k = _read_first_line(p)
        if k.startswith("sk-or-"):
            return k
    return ""


# -----------------------------------------------------------------------------
# Rules-based classification
# -----------------------------------------------------------------------------

_CMD = r"^\s*"

BASH_RULES = [
    # critical
    (rf"{_CMD}rm\s+-[rf]+\s*[/\\]\s*(\*|$)", "critical", "delete filesystem root"),
    (rf"{_CMD}(mkfs|fdisk|parted)\b",         "critical", "format disk"),
    (rf"{_CMD}dd\s+.*of=\s*/dev/",            "critical", "overwrite raw device"),
    (rf"{_CMD}(shutdown|reboot|halt|poweroff)\b", "critical", "power off system"),
    (r":\s*\(\s*\)\s*\{",                     "critical", "fork bomb"),
    # high
    (rf"{_CMD}git\s+push\s+.*(--force\b|\s-f\b)", "high", "force-push"),
    (rf"{_CMD}git\s+reset\s+--hard",          "high", "hard reset"),
    (rf"{_CMD}git\s+clean\s+-",               "high", "git clean untracked"),
    (rf"{_CMD}git\s+restore\b",               "high", "git restore (discard)"),
    (rf"{_CMD}rm\s+-[rf]*r[rf]*\b",           "high", "recursive delete"),
    (rf"{_CMD}rm\s+--recursive\b",            "high", "recursive delete"),
    (rf"{_CMD}drop\s+(table|database|schema)\b", "high", "drop table/db"),
    (rf"{_CMD}truncate\s+table\b",            "high", "truncate table"),
    (rf"{_CMD}(pkill|killall)\s+\S",          "high", "mass kill by name"),
    # medium: interpreter wrappers (captured as safety floor below)
    (rf"{_CMD}git\s+push\s+.*(--dry-run\b|\s-n\b)", "none", "git push --dry-run"),
    (rf"{_CMD}git\s+push\b",                  "low",    "git push"),
    (rf"{_CMD}git\s+commit\s+--amend",        "medium", "git commit --amend"),
    (rf"{_CMD}git\s+rebase\b",                "medium", "git rebase"),
    (rf"{_CMD}git\s+checkout\s+--\s",         "medium", "git checkout -- file"),
    (rf"{_CMD}rm\s+(?!--)\S",                 "medium", "delete file"),
    (rf"{_CMD}mv\s+\S",                       "medium", "move/rename"),
    (rf"{_CMD}docker\s+(rm|kill|stop)\b",     "medium", "stop container"),
    (rf"{_CMD}docker\s+compose\s+(down|stop)\b", "medium", "compose down/stop"),
    (rf"{_CMD}kill\s+\S",                     "medium", "kill process"),
    (rf"{_CMD}(npm|pnpm|yarn)\s+uninstall\b", "medium", "uninstall package"),
    (rf"{_CMD}(chmod|chown)\s+\S",            "medium", "change permissions"),
    (rf"{_CMD}gh\s+pr\s+merge\b",             "medium", "merge PR"),
    (rf"{_CMD}gh\s+repo\s+delete\b",          "critical", "delete repo"),
    # low: create / easy-rollback
    (rf"{_CMD}(npm|pip|pip3|pnpm|yarn)\s+install\b",       "low", "install package"),
    (rf"{_CMD}dotnet\s+(build|restore|publish)\b",          "low", "dotnet build"),
    (rf"{_CMD}git\s+commit\b",                             "low", "git commit"),
    (rf"{_CMD}git\s+(add|stash)\b",                        "low", "git stage/stash"),
    (rf"{_CMD}git\s+(pull|fetch)\b",                       "low", "git pull/fetch"),
    (rf"{_CMD}git\s+(checkout|switch)\b",                  "low", "git checkout branch"),
    (rf"{_CMD}mkdir\b",                                    "low", "create directory"),
    (rf"{_CMD}cp\s+\S",                                    "low", "copy file"),
    (rf"{_CMD}touch\b",                                    "low", "create file"),
    (rf"{_CMD}docker\s+(run|build|pull|tag|exec|attach|cp)\b", "low", "docker run/build"),
    (rf"{_CMD}docker\s+compose\s+(up|start|restart|run)\b",    "low", "docker compose up"),
    # interpreters (also anchored by _segment_floor)
    (rf"{_CMD}(python|python3|py)\s+-m\s+pytest",     "none",   "run pytest"),
    (rf"{_CMD}(python|python3|py)\s+test[\w_]*\.py\b","none",   "run test script"),
    (rf"{_CMD}(python|python3|py)\s+-c\b",            "medium", "python -c arbitrary code"),
    (rf"{_CMD}(python|python3|py)\s+-m\s+\w+",        "medium", "python -m module"),
    (rf"{_CMD}(python|python3|py)\b",                 "medium", "python script"),
    (rf"{_CMD}node\s+-e\b",                           "medium", "node -e arbitrary code"),
    (rf"{_CMD}node\s+(--check|--version|-v)\b",       "none",   "node check/version"),
    (rf"{_CMD}node\b",                                "medium", "node script"),
    (rf"{_CMD}bash\s+-c\b",                           "medium", "bash -c arbitrary code"),
    (rf"{_CMD}bash\s+",                               "medium", "bash script"),
    (rf"{_CMD}(powershell|pwsh)(\.exe)?\s+.*-Command\b",
                                                      "medium", "PowerShell -Command arbitrary code"),
    (rf"{_CMD}dotnet\s+test\b",               "none", "dotnet test"),
    (rf"{_CMD}dotnet\s+(run|exec)\b",         "low",  "dotnet run/exec"),
    (rf"{_CMD}(curl|wget)\b",                 "low",  "network fetch"),
    # safe read-only
    (rf"{_CMD}(ls|cat|head|tail|grep|rg|find|echo|pwd|whoami|which|where|type|stat|file|du|df|ps|top|htop|wc|sed|awk|cut|sort|uniq|tr|xargs|jq|printf|date|hostname|uname|env)\b",
                                              "none", "read-only"),
    (rf"{_CMD}git\s+(status|log|diff|show|branch|remote|describe|rev-parse|ls-files|blame|reflog|shortlog|tag)\b",
                                              "none", "git read-only"),
    (rf"{_CMD}git\s+config\s+--get\b",        "none", "git config read"),
    (rf"{_CMD}gh\s+(issue|pr|repo|release|run|workflow|project|search|api)\s+(list|view|status|search|diff|checks|show|get|read)\b",
                                              "none", "gh read-only"),
    (rf"{_CMD}docker\s+(ps|images|logs|inspect|info|version|stats|top|history)\b",
                                              "none", "docker read-only"),
    (rf"{_CMD}cd\b",                          "none", "cd"),
    (rf"{_CMD}sleep\s+\d",                    "none", "sleep"),
    (rf"{_CMD}#",                             "none", "comment"),
    (rf"{_CMD}tasklist(\.exe)?\b",            "none", "tasklist (Windows)"),
    (rf"{_CMD}netstat\b",                     "none", "netstat"),
    (rf"{_CMD}(ipconfig|hostname|whoami|systeminfo)\b",
                                              "none", "system info"),
    (rf"{_CMD}(Get-|Select-|Where-|Measure-|Format-|Out-|Test-Path|Read-Host|Write-Host|ConvertFrom-|ConvertTo-)\w+\b",
                                              "none", "PowerShell read-only"),
    (rf"{_CMD}(New-Item|Copy-Item|Move-Item|Set-Content|Add-Content)\b",
                                              "low", "PowerShell file edit"),
    (rf"{_CMD}(Remove-Item|rm-item|ri)\b",    "high", "PowerShell delete"),
    (rf"{_CMD}(Start-Process|Invoke-Expression|iex)\b",
                                              "medium", "PowerShell process start"),
]

_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4, "unknown": -1}


def _classify_segment(seg: str):
    for pattern, danger, human in BASH_RULES:
        if re.search(pattern, seg, re.IGNORECASE):
            return human, danger
    return None, None


def _check_composition_patterns(command: str):
    """Detect dangerous pipe compositions that per-segment max-risk misses."""
    if re.search(r'\b(curl|wget)\b.*\|\s*(bash|sh|python|python3|py|node)\b', command, re.IGNORECASE):
        return "download and execute", "high"
    return None, None


def _has_write_redirect(command: str) -> bool:
    """True when command writes output to a real file via > or >>."""
    s = re.sub(r'"[^"]*"', '""', command)
    s = re.sub(r"'[^']*'", "''", s)
    # echo "" > path — empty file creation, equivalent to touch
    if re.match(r'^\s*echo\s+""\s*>>?\s*\S', s):
        return False
    return bool(re.search(r'\s>>?\s+(?!/dev/null\b)\S', s))


def _strip_commit_message(command: str) -> str:
    """Remove heredoc body from git commit -m "$(cat <<'EOF'...EOF)" before analysis."""
    return re.sub(r'\$\(cat\s+<<\'?EOF\'?.*?EOF\s*\)', '""', command, flags=re.DOTALL)


def rules_classify(command: str):
    """Classify by rules, splitting compound commands on ; && || |.
    Returns (desc, danger) or (None, None) if any part is unknown."""
    cmd = _strip_commit_message((command or "").strip())
    if not cmd:
        return "empty", "none"

    comp_desc, comp_danger = _check_composition_patterns(cmd)
    if comp_danger:
        return comp_desc, comp_danger

    parts = re.split(r"\s*(?:;|&&|\|\||\|)\s*", cmd)
    worst_desc, worst_danger = None, None
    any_unknown = False
    for p in parts:
        p = p.strip()
        if not p:
            continue
        desc, danger = _classify_segment(p)
        if danger is None:
            any_unknown = True
            continue
        if worst_danger is None or _RANK[danger] > _RANK[worst_danger]:
            worst_desc, worst_danger = desc, danger
    if worst_danger is None:
        return None, None
    if any_unknown:
        return None, None

    if worst_danger in ("none", "low") and _has_write_redirect(cmd):
        return "write to file via redirect", "medium"

    return worst_desc, worst_danger


def is_complex(command: str) -> bool:
    c = _strip_commit_message(command or "")
    markers = ("$(", "`", "<<", "powershell -c", "powershell -command",
               "cmd /c", "bash -c", "-Command", "Add-Type", "Invoke-Expression")
    low = c.lower()
    return any(m.lower() in low for m in markers)


# -----------------------------------------------------------------------------
# Interpreter floor — LLM cannot downgrade these below "medium"
# -----------------------------------------------------------------------------

def _segment_floor(command: str):
    floor_rules = [
        (rf"{_CMD}(python|python3|py)\s+-c\b",                      "medium", "python -c arbitrary code"),
        (rf"{_CMD}node\s+-e\b",                                     "medium", "node -e arbitrary code"),
        (rf"{_CMD}bash\s+-c\b",                                     "medium", "bash -c arbitrary code"),
        (rf"{_CMD}sh\s+-c\b",                                       "medium", "sh -c arbitrary code"),
        (rf"{_CMD}(powershell|pwsh)(\.exe)?\s+.*-Command\b",        "medium", "PowerShell -Command arbitrary code"),
        (rf"{_CMD}cmd(\.exe)?\s+/c\b",                              "medium", "cmd /c arbitrary code"),
    ]
    for pattern, danger, human in floor_rules:
        if re.search(pattern, command or "", re.IGNORECASE):
            return human, danger
    return None, None


# -----------------------------------------------------------------------------
# LLM classification (OpenRouter Haiku)
# -----------------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """You are a shell-command classifier for an AI coding agent (primarily Windows dev workflows).

CORE RULE: classify by the command SHAPE, not by whether the specific args make it a no-op.
- "git checkout -- <any-file>"   -> medium, even if the file does not exist (user might typo a real one)
- "rm -rf <any-path>"            -> high, even if the path does not exist (one typo = catastrophe)
- "git reset --hard"             -> high, always
- "kill -0"                      -> none (PID check). Other "kill" -> medium
- "--dry-run", "--help", "-n" (no-op flags) -> none (true simulation)

Levels:
- none     read/view/navigate/simulate (ls, cat, git status, git push --dry-run, docker ps, cd)
- low      create / easy-rollback (npm install, git commit, mkdir, cp, touch)
- medium   single irreversible action (rm file, mv, git checkout -- file, docker stop, kill, chmod)
- high     mass-data-loss risk (rm -rf, git push --force, git reset --hard, git restore, drop table)
- critical system destruction (rm -rf /, mkfs, shutdown, dd of=/dev/*, fork bomb)

Compound commands (&&, ||, ;, |): take the MAX over segments.
Interpreters (python -c, powershell -Command, bash -c, node -e): analyse inner code.

OUTPUT: one line exactly
<short description 2-6 words> | <level>
no explanation, no markdown."""


def _cache_key(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
        tmp = CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception:
        pass


def llm_classify(command: str):
    key = read_openrouter_key()
    if not key:
        return None, None
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": command},
        ],
        "max_tokens": 80,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SEC).read())
        content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log_event({"phase": "llm_classify_error", "error": str(e)[:200]})
        return None, None
    parts = content.split("|", 1)
    if len(parts) != 2:
        return None, None
    desc = parts[0].strip()
    danger = parts[1].strip().lower()
    if danger not in _RANK or danger == "unknown":
        return None, None
    return desc[:60], danger


def describe_bash(command: str):
    """Classification pipeline:
       1) Interpreter floor (medium minimum).
       2) Simple commands -> rules.
       3) Complex or rule-unknown -> LLM, clamped by floor."""
    floor_desc, floor_danger = _segment_floor(command)

    if not is_complex(command):
        d, dn = rules_classify(command)
        if dn is not None:
            if floor_danger and _RANK[dn] < _RANK[floor_danger]:
                return floor_desc, floor_danger
            return d, dn

    key = _cache_key(f"classify:{command}")
    cache = _load_cache()
    if key in cache:
        entry = cache[key]
        desc, danger = entry.get("desc", "command"), entry.get("danger", "unknown")
        if floor_danger and _RANK.get(danger, -1) < _RANK[floor_danger]:
            return floor_desc, floor_danger
        return desc, danger

    d, dn = llm_classify(command)
    if dn is None:
        d2, dn2 = rules_classify(command)
        if dn2 is not None:
            if floor_danger and _RANK[dn2] < _RANK[floor_danger]:
                return floor_desc, floor_danger
            return d2, dn2
        if floor_danger:
            return floor_desc, floor_danger
        first = (command or "").strip().split()
        return f"command {os.path.basename(first[0])[:20] if first else '?'}", "unknown"

    if floor_danger and _RANK[dn] < _RANK[floor_danger]:
        cache[key] = {"desc": floor_desc, "danger": floor_danger, "source": "floor"}
        _save_cache(cache)
        return floor_desc, floor_danger

    cache[key] = {"desc": d, "danger": dn, "source": "llm"}
    _save_cache(cache)
    return d, dn


# -----------------------------------------------------------------------------
# Haiku decision layer (medium danger only)
# -----------------------------------------------------------------------------

def _build_decision_prompt(cfg: dict) -> str:
    crit_files = ", ".join(cfg.get("critical_files", []))
    crit_dirs = ", ".join(cfg.get("critical_dirs", []))
    dev_procs = ", ".join(cfg.get("development_processes", []))
    return f"""You are a safety gate for a Windows developer AI agent.
Input: a shell command plus its description and danger level.
Output: one word — "yes" (silently allow) or "no" (prompt the user).

Allow ("yes") when — check these FIRST, before deny rules:

1) SAME-ORIGIN authentication — a token is read and used ONLY against that
   tool's canonical API endpoint. This is routine, NOT exfiltration.
   Explicit allow patterns (include all of these, even if wrapped in python -c,
   bash -c, subprocess, urllib, requests, curl, gh itself):
   * gh auth token used in Authorization header -> api.github.com
   * docker login / docker credential helper -> *.docker.io
   * OpenAI SDK reading OPENAI_API_KEY -> api.openai.com
   * Anthropic SDK reading ANTHROPIC_API_KEY -> api.anthropic.com
   * npm/pip CLI reading .npmrc / .pypirc -> registry.npmjs.org / pypi.org
   Concrete example that MUST be allowed: a python -c that runs
   subprocess.run(['gh','auth','token']), takes the output, and sends it
   via urllib.request to api.github.com in an Authorization header.
   The token goes only to api.github.com -> ALLOW.

2) TRUSTED SKILL EXECUTION — script or inline code is part of a Claude Code
   skill the user has installed. ALLOW even when wrapped in python -c / bash -c.
   Trusted locations: `~/.claude/skills/…`, `~/.claude/plugins/…`,
   `<project>/.claude/skills/…`. The skill path appears as an argument or as
   embedded working context — NOT as the target of rm/mv/chmod.

3) Typical dev/test/deploy workflow — git push, git commit, docker build/run/stop,
   dotnet build/test, npm install, pytest. Includes running project scripts from the
   working directory: python run.py, bash build.sh, bash test.sh, node start.js.
   Includes launching a user-installed app for QA via Start-Process on an .exe under
   AppData/Local/Programs/*, Program Files/*, or the project directory.
   System read-only utilities: tasklist, netstat, ipconfig, whoami, hostname, systeminfo.

4) Tempfile/log/build-artefact maintenance — mv/cp/rm on bin/, obj/, *.log, *.tmp,
   /tmp/*, AppData/Local/Temp/*.

5) kill/pkill for development processes: {dev_procs}

6) INTERPRETER body only does read / output / arithmetic / imports without
   destructive calls (length and complexity don't matter — a long read-only
   pipeline is still read-only):
   - print(...), echo, Write-Host, Write-Output
   - Get-* cmdlets including Get-Content, Get-ChildItem, Get-Process, Get-WmiObject,
     Get-CimInstance, Get-EventLog, Get-WinEvent, Get-Service, Get-ComputerInfo
     (all without -Force). These are read-only QUERIES.
     Invoke-WmiMethod and Invoke-CimMethod are separate and NOT allowed here.
   - Pipeline-only: Where-Object, Select-Object, Sort-Object, Measure-Object,
     Group-Object, ForEach-Object, Format-Table, Format-List, Out-String, Out-Host
   - ConvertFrom-Json, ConvertTo-Json, ConvertFrom-Csv and other Convert* cmdlets
   - Variable assignment ($var = ...) when the right side is read-only
   - Hash tables @{{...}} and script blocks {{$_.prop}} used with Select-Object/Where-Object
   - read-only HTTP (requests.get, urllib.urlopen, curl without -o to system path)
   - JSON/XML parsing, math
   - Reading any config or metadata file (open(path, 'r'), json.load, Get-Content) —
     any path including .claude/, settings.json, package.json, *.csproj
   - Creating new files/directories/junctions in project or user paths
     (New-Item including -ItemType Junction/SymbolicLink, mkdir, touch, Copy-Item to new path,
     Set-Content/Add-Content on a new file). Creation is NOT destruction.
     Deny only if the path overwrites a CRITICAL artefact listed below or sits in a system directory.

Deny ("no") when:
- Command touches SYSTEM paths (/, /c/Windows, /c/Program Files, /c/ProgramData)
- Command touches USER SECRETS (.ssh/, .gnupg/, .env*, *credentials*, *token*, *.key, *.pem)
  AND forwards them to a non-matching destination or writes them to a new file/location.
  (Reading a same-origin token as in "Allow" above is NOT this case.)
- Command deletes/moves/overwrites CRITICAL PROJECT ARTEFACTS
  (write/delete/move only; read via open('r'), json.load, cat is ALLOWED):
  * files: {crit_files}
  * dirs:  {crit_dirs}
  * markdown docs outside /archive/ or similar
- Command moves/deletes a whole project directory (mv/rm of a folder)
- Looks like exploit, data exfiltration, or mass overwrite
- kill PID < 100 (system processes) or kill 1 (init)
- INTERPRETER body contains destructive calls:
  * shutil.rmtree, os.remove, os.unlink, pathlib.*.unlink
  * subprocess with rm/del/Remove-Item/rd
  * Remove-Item with -Force/-Recurse, Clear-RecycleBin
  * network-load + write-to-file + exec pattern (curl|iwr → Invoke-Expression)
  * P/Invoke to win32 API, Add-Type with native code
  * Invoke-WmiMethod / Invoke-CimMethod (can create processes or modify system)

When in doubt, deny. The user can still approve via the dialog.

FORMAT: strict JSON on a single line, no code fences:
{"verdict":"yes|no","reason":"short phrase, <60 chars"}"""


def _parse_verdict_json(text: str) -> tuple[bool, str]:
    """Parse LLM answer: strict JSON first, fallback to 'yes'/'no' keyword."""
    t = (text or "").strip()
    # strip optional code fences
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    try:
        obj = json.loads(t)
        v = str(obj.get("verdict", "")).strip().lower()
        reason = str(obj.get("reason", "") or "")[:120]
        return (v == "yes", reason)
    except Exception:
        # legacy single-word answer
        low = t.lower()
        return (low.startswith("yes"), "")


def _run_custom_verifier(cmd_path: str, payload: dict) -> tuple[bool, str] | None:
    """Run user-supplied verifier script. Returns (allow, reason) or None on error.
    Script receives payload as JSON on stdin, returns {"allow": bool, "reason": str} on stdout."""
    import subprocess
    try:
        proc = subprocess.run(
            cmd_path, shell=True, input=json.dumps(payload),
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0:
            log_event({"phase": "verifier_nonzero", "rc": proc.returncode,
                       "stderr": (proc.stderr or "")[:200]})
            return None
        obj = json.loads(proc.stdout or "{}")
        return (bool(obj.get("allow", False)), str(obj.get("reason", ""))[:120])
    except Exception as e:
        log_event({"phase": "verifier_error", "error": str(e)[:200]})
        return None


def ask_haiku(tool_name: str, tool_input: dict, desc: str, danger: str) -> tuple[bool, str]:
    """Returns (allow, reason). Fail-closed on error."""
    cmd = str(tool_input.get("command") or tool_input.get("file_path") or "")
    try:
        cwd = os.getcwd()
    except Exception:
        cwd = "?"
    cache_key_str = f"haiku_decision:v3:{tool_name}:cwd={cwd}:{cmd}"
    key = _cache_key(cache_key_str)
    cache = _load_cache()
    if key in cache:
        entry = cache[key]
        log_event({"phase": "haiku_cached", "desc": desc,
                   "verdict": entry.get("verdict"), "reason": entry.get("reason", "")})
        return (entry.get("verdict", False), entry.get("reason", ""))

    # Custom verifier takes precedence if configured
    verifier_cmd = os.environ.get("HAIKU_GUARD_VERIFIER_CMD", "").strip()
    if verifier_cmd:
        payload = {"tool": tool_name, "command": cmd, "cwd": cwd,
                   "description": desc, "danger": danger}
        result = _run_custom_verifier(verifier_cmd, payload)
        if result is None:
            log_event({"phase": "verifier_fail_closed"})
            return (False, "custom verifier error")
        verdict, reason = result
        log_event({"phase": "verifier_decision", "verdict": verdict, "reason": reason})
        cache[key] = {"verdict": verdict, "reason": reason, "desc": desc, "cwd": cwd,
                      "source": "custom"}
        _save_cache(cache)
        return (verdict, reason)

    or_key = read_openrouter_key()
    if not or_key:
        log_event({"phase": "haiku_no_key_fail_closed", "desc": desc})
        _notify(
            "Haiku Guard — key not found",
            "No OpenRouter key found.\n\n"
            "Expected: ~/.openrouter_key  (one line: sk-or-...)\n"
            "or env var: HAIKU_GUARD_OPENROUTER_KEY\n\n"
            "Medium-risk commands will show a dialog until this is fixed.",
        )
        return (False, "no OpenRouter key")

    cfg = _load_config()
    user_msg = f"Tool: {tool_name}\nCWD: {cwd}\nCommand: {cmd}\nDescription: {desc}\nLevel: {danger}"
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": _build_decision_prompt(cfg)},
            {"role": "user", "content": user_msg},
        ],
        "max_tokens": 80,
        "temperature": 0,
    }).encode("utf-8")
    req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
        "Authorization": f"Bearer {or_key}",
        "Content-Type": "application/json",
    })
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SEC).read())
        answer = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except urllib.error.HTTPError as e:
        code = e.code
        if code in (401, 403):
            _notify(
                "Haiku Guard — key rejected",
                f"OpenRouter returned HTTP {code}.\n\n"
                "Check your key in ~/.openrouter_key\n"
                "or HAIKU_GUARD_OPENROUTER_KEY.",
            )
        elif code in (402, 429):
            _notify(
                "Haiku Guard — credits or rate limit",
                f"OpenRouter returned HTTP {code}.\n\n"
                "Your account may be out of credits or rate-limited.\n"
                "Top up at: openrouter.ai/settings/credits",
            )
        log_event({"phase": "haiku_error_fail_closed", "error": f"HTTP {code}"})
        return (False, f"HTTP {code}")
    except Exception as e:
        log_event({"phase": "haiku_error_fail_closed", "error": str(e)[:200]})
        return (False, "network error")

    verdict, reason = _parse_verdict_json(answer)
    log_event({"phase": "haiku_decision", "desc": desc, "danger": danger,
               "answer": answer, "verdict": verdict, "reason": reason})
    cache[key] = {"verdict": verdict, "reason": reason, "answer": answer,
                  "desc": desc, "cwd": cwd, "source": "haiku"}
    _save_cache(cache)
    return (verdict, reason)


# -----------------------------------------------------------------------------
# Tool description (non-Bash tools)
# -----------------------------------------------------------------------------

def describe(tool_name: str, tool_input: dict):
    try:
        if tool_name == "Bash":
            return describe_bash(str(tool_input.get("command", "") or ""))
        if tool_name == "Write":
            fn = os.path.basename(str(tool_input.get("file_path", "?")))
            return f"write {fn}", "low"
        if tool_name == "Edit":
            fn = os.path.basename(str(tool_input.get("file_path", "?")))
            return f"edit {fn}", "low"
        if tool_name == "WebFetch":
            url = str(tool_input.get("url", "?"))
            host = re.sub(r"^https?://", "", url).split("/")[0][:30]
            return f"fetch {host}", "low"
        return f"tool {tool_name}", "unknown"
    except Exception:
        return f"tool {tool_name}", "unknown"


# -----------------------------------------------------------------------------
# Decision emitters — PreToolUse and PermissionRequest wire formats differ
# -----------------------------------------------------------------------------

# Patterns for CATASTROPHIC commands — never legitimate in a dev workflow,
# and a single accidental "Yes" click would be unrecoverable.
# When matched, the hook replaces the command with a harmless echo and asks
# the user to review the chat — agent workflow pauses on the ask dialog.
CATASTROPHIC_PATTERNS = [
    (r"^\s*rm\s+(?:-\S*\s+)*/\s*(?:$|[;&|])",                "rm of root /"),
    (r"^\s*rm\s+(?:-\S*\s+)*/\*",                            "rm of root contents /*"),
    (r"^\s*rm\s+(?:-\S*\s+)*(?:~|\$HOME)/?\s*(?:$|[;&|])",   "rm of home directory"),
    (r"^\s*rm\s+(?:-\S*\s+)*/c/?\s*(?:$|[;&|])",             "rm of Windows C: drive"),
    (r"^\s*rm\s+(?:-\S*\s+)*/c/\*",                          "rm of Windows C: contents"),
    (r"^\s*dd\s+.*\bof=/dev/(?:sd|nvme|hd|vd|xvd)",          "dd raw write to disk device"),
    (r"^\s*mkfs(?:\.\w+)?\s+/dev/(?:sd|nvme|hd|vd|xvd)",     "mkfs format of disk device"),
    (r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:\s*&[^}]*\}\s*;?\s*:", "fork bomb"),
    (r"^\s*chmod\s+.*-R\s+777\s+(?:/|~|\$HOME)\s*(?:$|[;&|])", "chmod -R 777 on root or home"),
]


def _is_catastrophic(command: str) -> str | None:
    """Return the human description if command matches any catastrophic pattern, else None."""
    if not command:
        return None
    for pattern, label in CATASTROPHIC_PATTERNS:
        if re.search(pattern, command):
            return label
    return None


def _emit_catastrophic_intercept(description: str, original_cmd: str,
                                 event_name: str = "PreToolUse"):
    """Replace destructive command with a safe echo and ask user to review chat."""
    # The echo text is a plain literal string — no shell expansion of the original cmd.
    safe_echo = (
        'echo "CATASTROPHIC COMMAND BLOCKED by haiku-guard. '
        f'Type: {description}. Review chat context before acting."'
    )
    reason = (
        f"CATASTROPHIC intercept: agent attempted {description}. "
        "Command has been replaced with a safe echo. "
        "Click No to block. Click Yes only after reviewing the chat."
    )
    agent_notice = (
        "SECURITY ALERT: Your previous tool call was a CATASTROPHIC destructive "
        "command (type: " + description + "). It has been intercepted by the "
        "security hook and replaced with a safe echo. You MUST stop executing "
        "any further commands, acknowledge this to the user, and wait for "
        "explicit user instructions. Do NOT attempt workarounds or alternative "
        "paths to achieve the blocked action."
    )
    out = {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason,
        "updatedInput": {"command": safe_echo},
        "additionalContext": agent_notice,
    }}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    log_event({"phase": "catastrophic_intercept", "description": description,
               "original_cmd": original_cmd[:300]})

    # Also notify user via MessageBox — visible even if the Claude Code window is
    # minimized or in the background.
    _notify(
        "Haiku Guard — CATASTROPHIC command intercepted",
        "ATTENTION!\n\n"
        f"Claude Code agent attempted: {description}\n\n"
        f"Original command:\n{original_cmd[:300]}\n\n"
        "Review the chat in Claude Code.",
        icon="error",
    )


def _emit(event_name: str, decision: str, reason: str):
    if event_name == "PreToolUse":
        out = {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }}
    else:  # PermissionRequest
        if decision == "ask":
            log_event({"phase": "ask_via_empty_stdout", "reason": reason})
            return
        body = {"behavior": decision}
        if decision == "deny":
            body["message"] = f"Denied: {reason}"
        out = {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                      "decision": body}}
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    log_event({"phase": decision, "event": event_name, "reason": reason})


def _emit_allow(reason: str, event_name: str = "PermissionRequest"):
    _emit(event_name, "allow", reason)


def _emit_deny(reason: str, event_name: str = "PermissionRequest"):
    _emit(event_name, "deny", reason)


def _emit_ask(reason: str, event_name: str = "PermissionRequest"):
    _emit(event_name, "ask", reason)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        log_event({"phase": "stdin_error"})
        return

    event_name = payload.get("hook_event_name") or "PermissionRequest"
    tool_name = payload.get("tool_name", "?")
    tool_input = payload.get("tool_input") or {}

    # Catastrophic intercept — runs BEFORE normal classification.
    # Only meaningful for PreToolUse (updatedInput is a PreToolUse-only feature).
    if event_name == "PreToolUse" and tool_name == "Bash":
        raw_cmd = str(tool_input.get("command") or "")
        catastrophic = _is_catastrophic(raw_cmd)
        if catastrophic:
            _emit_catastrophic_intercept(catastrophic, raw_cmd, event_name)
            return

    desc, danger = describe(tool_name, tool_input)

    log_event({
        "phase": "classified",
        "event": event_name,
        "tool": tool_name,
        "desc": desc,
        "danger": danger,
        "cmd_preview": str(
            tool_input.get("command") or tool_input.get("file_path") or tool_input.get("url") or ""
        )[:200],
    })

    if danger in ("none", "low"):
        _emit_allow(f"auto: {danger}: {desc}", event_name)
        return

    if danger == "medium":
        allow, reason = ask_haiku(tool_name, tool_input, desc, danger)
        tail = f" — {reason}" if reason else ""
        if allow:
            _emit_allow(f"haiku: {desc}{tail}", event_name)
        else:
            _emit_ask(f"medium / haiku-denied: {desc}{tail}", event_name)
        return

    # high / critical / unknown — always surface dialog
    _emit_ask(f"{danger}: {desc}", event_name)


if __name__ == "__main__":
    main()
