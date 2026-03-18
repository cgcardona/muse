# Muse Tools

## Tour de Force

`tour_de_force.py` runs a 5-act VCS stress test against a real (temporary) Muse
repository and renders a shareable, self-contained HTML visualization.

### Run

```bash
# From the repo root, with the venv active:
python tools/tour_de_force.py
```

Output lands in `artifacts/` (gitignored):

| File | Description |
|---|---|
| `artifacts/tour_de_force.json` | Structured event log + full commit DAG |
| `artifacts/demo.html` | Self-contained shareable visualization |

Open the HTML in any browser:

```bash
open artifacts/demo.html        # macOS
xdg-open artifacts/demo.html   # Linux
```

### Options

```
--output-dir PATH   Write output here (default: artifacts/)
--json-only         Skip HTML rendering, emit JSON only
```

### What the 5 acts cover

| Act | Operations |
|---|---|
| 1 · Foundation | `init`, 3 commits on `main` |
| 2 · Divergence | 3 branches (`alpha`, `beta`, `gamma`), 5 branch commits |
| 3 · Clean Merges | `merge alpha`, `merge beta` — auto-resolved two-parent commits |
| 4 · Conflict & Resolution | `conflict/left` + `conflict/right` → CONFLICT → manual resolve + commit |
| 5 · Advanced Ops | `cherry-pick`, `show`, `diff`, `stash`, `stash pop`, `revert`, `tag`, `log` |

---

## Typing Audit

`typing_audit.py` scans the codebase for banned typing patterns (enforcing the
"ratchet of zero" policy from `AGENTS.md`).

```bash
python tools/typing_audit.py --dirs muse/ tests/ --max-any 0
```
