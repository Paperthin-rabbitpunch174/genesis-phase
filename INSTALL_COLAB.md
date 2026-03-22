# GENESIS PHASE — Colab Installation

> 5 minutes. 4 secrets. 2 cells. Done.

---

## Option A: Use the setup wizard (recommended)

If you haven't pushed to GitHub yet:

```
cd genesis-phase
python3 setup.py
```

The wizard validates your keys, creates the repo, pushes files, and generates a ready-to-run Colab notebook (`GENESIS_PHASE.ipynb`). Upload that notebook to Colab, add the 4 secrets, run both cells. Done.

---

## Option B: Manual setup

### Step 1: Add secrets

Click the **🔑 key icon** in Colab's left sidebar. Add these 4 secrets:

| Name (type exactly) | Value |
|-----|-------|
| `OPENROUTER_API_KEY` | Your `sk-or-...` key from openrouter.ai/keys |
| `TELEGRAM_BOT_TOKEN` | Your bot token from @BotFather |
| `GITHUB_TOKEN` | Your `ghp_...` token from github.com/settings/tokens |
| `TOTAL_BUDGET` | `50` (or any max USD amount) |

**⚠️ Toggle each secret ON** (the switch next to each one must be blue).
This is the #1 setup mistake — if the switches aren't blue, the agent can't read the secrets.

### Step 2: Mount Google Drive

Add a cell and run it first — before anything else:

```python
from google.colab import drive
drive.mount("/content/drive")
```

A popup asks for permission — click Allow. This only needs to be done once per session. Drive stores all persistent state (memory, logs, chat history).

### Step 3: Config cell

```python
import os
os.environ["GITHUB_USER"] = "YOUR_USERNAME"   # ← your GitHub username
os.environ["GITHUB_REPO"] = "genesis-phase"   # ← your repo name
```

### Step 4: Launch cell

If using the ZIP (testing/first time):

```python
!unzip -o /content/genesis-phase.zip -d /content/ouroboros_repo 2>/dev/null
%cd /content/ouroboros_repo
!pip install -q openai requests

# Create ouroboros branch if missing (first run only)
import subprocess, pathlib
_DIR = "/content/ouroboros_repo"
_rc = subprocess.run(
    ["git", "rev-parse", "--verify", "origin/ouroboros"],
    cwd=_DIR, capture_output=True
).returncode
if _rc != 0:
    subprocess.run(["git", "checkout", "-b", "ouroboros"], cwd=_DIR, check=False)
    subprocess.run(["git", "push", "-u", "origin", "ouroboros"], cwd=_DIR, check=False)
    print("✓ ouroboros branch created")

%run colab_launcher.py
```

If using your GitHub repo (after first push):

```python
from google.colab import userdata
import subprocess, pathlib

_TOKEN = userdata.get("GITHUB_TOKEN")
_USER = os.environ["GITHUB_USER"]
_REPO = os.environ["GITHUB_REPO"]
_URL = f"https://{_TOKEN}:x-oauth-basic@github.com/{_USER}/{_REPO}.git"
_DIR = pathlib.Path("/content/ouroboros_repo")

if not (_DIR / ".git").exists():
    subprocess.run(["rm", "-rf", str(_DIR)], check=False)
    subprocess.run(["git", "clone", _URL, str(_DIR)], check=True)
else:
    subprocess.run(["git", "-C", str(_DIR), "pull", "--rebase"], check=False)

%cd /content/ouroboros_repo
!pip install -q openai requests
%run colab_launcher.py
```

### Step 5: Open Telegram

Find your bot. Send any message. Wait 5-10 seconds.

First message: "✅ Owner registered. GENESIS PHASE online."

---

## Commands

| Command | What it does |
|---------|-------------|
| `/status` | System status, budget, workers |
| `/evolve on/off` | Self-evolution mode |
| `/bg start/stop` | Background consciousness |
| `/review` | Trigger code review |
| `/restart` | Soft restart |
| `/panic` | Emergency stop |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `credential propagation was unsuccessful` | Mount Drive first: `drive.mount("/content/drive")` in a separate cell before launching |
| `Branch ouroboros not found on remote` | The launch cell now auto-creates the branch. If using old notebook, add the branch creation snippet from Step 4 |
| `Bootstrap failed` | Now a warning, not a crash. Agent continues without git sync. Fix: ensure ouroboros branch exists |
| `budget: unconfigured` | Fixed: `TOTAL_BUDGET` now exported to worker env vars |
| `Pre-push tests failed, blocking push` | Test gate is OFF by default. Pre-existing code quality issues don't block the agent |
| Missing secrets error | All 4 secrets must be toggled ON (blue switch) in the 🔑 sidebar |
| Bot not responding | Check OpenRouter balance. Send `/status` to check budget |
| Colab disconnects | Re-run all cells. State is on Drive — nothing lost |
