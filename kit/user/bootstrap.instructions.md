---
description: "Always-on (user level). Greg's cross-workspace practices: greg.md is the entry point of every repo; set up / move / resume machines; adopt the BestPractices kit when missing."
applyTo: "**"
---
# Greg's workspaces (user-level pointer; keep tiny)

- Greg's practices for ANY workspace: https://github.com/codenotes666-blip/BestPractices
  (`greg_best_practices.md`, public). Read it when setting up, moving or changing how a
  workspace is organized, or when Greg says "best practices".
- **"read greg.md"** = do what that repo's `greg.md` says (new machine / move / resume).
  No greg.md yet -> adopt the kit (`greg_best_practices.md` section 4) and write one.
- **Repo has `scripts/wake.py`** and no `WAKE` block is in context: run it first.
- **Leaving a machine:** update `SESSION_STATE.md`, run `python scripts/hibernate.py "<note>"`.
- Code on local disk via GitHub; never a git checkout inside OneDrive. Stdlib Python for tooling.
