# greg.md - the ONE file Greg tells any agent to read ({name})

Greg only says: **"read greg.md"**. Agent: work out which section applies (1-5) and
do it yourself, top to bottom. Install tools, clone, build the environment - do not
hand Greg setup steps. He only clicks sign-ins (GitHub, OneDrive) when prompted.
General practices behind this file: https://github.com/codenotes666-blip/BestPractices

## Where everything lives

| What | Where | Moves between machines by |
|---|---|---|
| **Code** | GitHub `{owner_repo}`, checked out on LOCAL disk: Mac `~/code/{name}`, Windows `%USERPROFILE%\code\{name}` | git push / pull (Greg approves commits) |
| **Live data** | TODO: none / cloud folder (e.g. OneDrive `{name}`) holding the data files | TODO: OneDrive, automatically |
| **Where we left off** | `SESSION_STATE.md` - TODO: in the cloud data folder (if any) or the checkout | TODO: OneDrive / hibernate commit |
| **Python env** | `~/.venvs/{name}` (Windows `%USERPROFILE%\.venvs\{name}`) - never inside a cloud folder | built per machine |
| **Secrets** | TODO: `.env` from ... | per machine |

Below, `PY` = the venv python and `CODE` = the local checkout.

## 1. Which situation are you in?

- Your workspace is the local checkout (`CODE`) -> **4. RESUME**.
- Your workspace is a git checkout inside OneDrive/iCloud/Dropbox -> **3. MOVE THIS MACHINE**.
- No checkout on this machine -> **2. NEW MACHINE**.

## 2. NEW MACHINE

1. Install anything missing (git, Python >= 3.11, gh, TODO) and sign in to GitHub
   (`gh auth login --web`; Greg clicks Authorize).
2. TODO (if there is a data folder): Greg signs in to OneDrive; wait until it has synced.
3. `git clone https://github.com/{owner_repo}.git CODE`
4. `python3 -m venv ~/.venvs/{name}` then TODO: `PY -m pip install -r CODE/requirements.txt`
5. TODO: secrets.
6. TODO: a check that proves the code sees its data.
7. Ask Greg to open `CODE` in VS Code. -> **4. RESUME**.

## 3. MOVE THIS MACHINE (git checkout inside a cloud-synced folder)

Never delete, reset or `git clean` anything in the cloud folder: it may hold live
data and the other machines' unsaved work.

1. Code here that is not on GitHub? `git status --short`, `git log --oneline @{u}..`
   (ignore data and line-ending-only noise). Show real changes to Greg; ask whether
   to commit + push them.
2. NEW MACHINE steps 3-5; copy secrets from the old folder (do not print them).
3. TODO: restart anything that ran from the old folder.
4. `PY CODE/scripts/wake.py` must run clean. Tell Greg to open `CODE` from now on.
5. Remove the code from the cloud folder only after EVERY machine has moved and
   Greg approves.

## 4. RESUME - where we left off

1. `cd CODE` and `PY scripts/wake.py`: it syncs code safely and prints the other
   machines' unpushed work (tell Greg before touching the same files) and
   `SESSION_STATE.md` - where we left off.
2. Follow `AGENTS.md`.
3. Tell Greg in 3-5 lines where we left off and what's next, then ask what he wants.

## 5. Before leaving a machine

1. Update `SESSION_STATE.md`: what we did / how to resume / open TODOs.
2. `PY scripts/hibernate.py "<one-line note>"`.
3. If code changed, tell Greg it reaches the other machines only after he approves
   a commit + push.
