---
name: audit-conformance
description: Periodic whole-codebase audit of actop against the coding rules baked into this skill (boundaries, one-sided members, underscore leaks, dead code, DRY, naming, swallowed errors). Inventories accreted violations with file:line + rule citation, then writes a scoped cleanup report. The whole-tree counterpart to diff-scoped /code-review — catches slow accretion that reviewing a single diff is blind to. Never proposes guard/structural tests.
argument-hint: "[module-or-path scope, default whole actop/]"
---

# audit-conformance

**Invocation:** `/audit-conformance [scope]` (default scope: all of `actop/`)

**Mission:** `/code-review` catches violations a *change* introduces; it is structurally
blind to slow whole-codebase accretion. This skill is the other scope: judgment-scan the
whole tree against the coding rules **defined in this file**, inventory every violation
with `file:line` + the exact rule cited, and write a scoped cleanup report.

**Why this exists:** global invariants (clean layering, stable public surface, DRY, no
dead code) erode because they are defended only at the diff. Twenty good-local refactors
leave a residue surface nobody owns. This skill owns the surface across time.

**This skill is self-contained.** Every rule it scans for is defined below — it does not
depend on any external rules folder. It only cross-references the in-repo `CLAUDE.md` for
two project-specific facts it must honor (the venv executables and the functional-tests
mandate); those are restated inline where they matter.

**Hard doctrine — read before producing output:**
- **Never propose a guard test / fitness function / "conformance test".** `CLAUDE.md`
  mandates *functional tests only* — a structural test passes against a gutted body and
  is itself a violation. The output is a *cleanup report that fixes code at the source*,
  never a test that freezes the violation behind an allowlist. Eliminating a violation
  class structurally (relocate a shared helper so the back-edge cannot exist) is stronger
  than detecting it.
- **Ground every finding in source.** Each row cites `file:line` and the exact rule (Rn).
  Never assert a violation from a name or call-shape without reading the implementing
  line. No "looks like" findings.
- **This skill does not edit code.** It produces an inventory + a cleanup report. Fixes
  run through the normal flow: a feature branch → PR → `/code-review` → merge (per
  `CLAUDE.md` — `main` is PR-only).

**Produces:** `docs/TODO-conformance-YYYY-MM-DD.md` (an actionable cleanup checklist —
matches the repo's `TODO-*` doc convention; `REVIEW-*` is reserved for architectural
analyses) + a terminal inventory summary.

**Environment (from `CLAUDE.md`):** use `.venv/bin/python` for any script; never global
`python`. `rg` and `uv` are available. Put all scratch scripts under `tmp/` (create it if
absent) — never in the repo root, never in `tests/`.

---

## The rule set (what gets scanned — the complete, self-contained definitions)

Every finding maps to exactly one of these. There is no external source of truth; these
definitions *are* the source of truth for the audit.

| # | Rule | What it means | Primary detector |
|---|------|---------------|------------------|
| R1 | **One-sided member** | A field / param / flag / dict key with only a write site OR only a read site. A value written but never read (or read but never written) is dead weight. **Sub-pattern — wiring gap (NOT dead):** a write-only field whose name/purpose matches a *hardcoded constant* used elsewhere (e.g. a config `*_window` that a deque hardcodes as `maxlen=500`) is a config knob the consumer ignores — the fix is a *decision* (wire up ⇒ behavior change, or delete as "hardcoded is intended"), not a mechanical prune. | grep the name **both as a bare word AND as a quoted string** (`getattr(cfg, "field")` reads won't show up in a bare-word grep — this is the #1 R1 false positive, e.g. a config field read only via `getattr`); one missing = dead. Also beware serialization (`asdict`), `**kwargs`, and dataclass/NamedTuple auto-reads. For a write-only field, grep the codebase for a hardcoded literal doing that field's job before proposing delete. |
| R2 | **Redundant same-lifecycle state** | Two flags / attributes / code paths that are always written together and cleared together — they encode one concept and should be one. | read mutation sites; co-set / co-cleared pairs. |
| R3 | **Wrapper bag / premature abstraction** | A class/dataclass that exists only as a return-value bag or one-liner passthrough with a single caller; *or* an ABC/Protocol/base class / factory whose only concrete implementation is one type. | classes whose only methods are `__init__`/field access with a single caller; ABC/Protocol with one subclass. A deliberate published-API seam or test seam is NOT a violation — confirm. |
| R4 | **Wrong module home / layer back-edge** | Domain logic in a module that doesn't own the concern, or a lower layer importing a higher one (see the actop layer order below). | the import edge map (built in Pass 0), MODULE-scope edges only. |
| R5 | **Underscore visibility leak** | An underscore-prefixed module or symbol (`_x`) imported across a package boundary (i.e. into `actop/tui/` from `actop/`, or vice versa). Same-package `_x` imports are legal. | the cross-package edge map filtered to PRIVATE names — NOT a flat grep. |
| R6 | **Import-time side effect** | Module top-level (import-time) statements that do IO, read config/env, build a console/logger/tracer, open a device, or construct a singleton. Import must be free of observable effects. **Carve-out (like R12's):** a load that is *platform/capability-guarded* (`if sys.platform == "darwin":` …) and effect-free (a `dlopen` of a system framework the OS caches) is acceptable — flag only **unguarded** module-scope IO. The inconsistency itself is the smell: if one ctypes module guards its load and its siblings don't, the unguarded ones are the finding. | AST/grep: module-scope statements (not under a `def`/`class`/`if <guard>:`) that are not `import`/constant/`__all__` and that *call or construct*. Read the surrounding block — a guarded load is not a hit. |
| R7 | **Optimistic flag** | `self.x = True` (or a "done"/"ready"/"subscribed" flag) set *before* the operation it asserts has actually committed — so a failure mid-op leaves a lying flag. | read the order of the flag-set vs the op it guards (subscribe, open, sample, write). |
| R8 | **Backward-compat residue** | An alias, compat shim, `_legacy`/`_compat`/`_old`, dual-format reader, or migration path kept "just in case" with no live producer of the old format. | grep `_legacy\|_compat\|\b_old\b`, alias assignments, dual-format branches; confirm the old format has no producer. |
| R9 | **Naming drift** | Wrong/absent unit suffix on a numeric (watts → `_w`, MHz → `_mhz`, bytes → `_bytes`, percent → `_pct`/`_percent`, seconds → `_s`), abbreviation where the codebase spells it out, or inconsistency with the established parser/metric keys (`P-Cluster_active`, `gpu_W`, `cpu_watts`, `ecpu_freq_mhz`). `CLAUDE.md`: *keep parser keys and metric field names consistent with existing patterns.* | section-by-section scan; compare against `models.py` field names and existing sampler keys. |
| R10 | **Dead code / dependency drift** | A function / method / symbol with zero non-test callers; a stale import; a declared dependency in `pyproject.toml` with zero import sites (**unused**); **or the inverse** — a third-party module imported in `actop/` that is declared only *transitively* (e.g. `rich`, pulled in by `textual`), not as a direct dep. The transitive one works until the upstream drops/vendors it. | grep caller count per symbol; `ruff check` for imports; diff `[project.dependencies]` against the actual import map **both ways** (resolve dist-name ≠ import-name, e.g. `textual`, `psutil`, `pydantic-ai`). |
| R11 | **Duplication (DRY)** | Near-identical block / logic / constant reimplemented in ≥2 homes — e.g. the same watt-scaling, color-mapping, or formatting primitive copied instead of shared. | clustered logic; same primitive reimplemented across `tui/`, `utils`, `power_scaling`. |
| R12 | **Swallowed error** | A broad `except Exception`/bare `except:`, an empty handler, or log-and-continue on a path where the user should see the failure — masking a real fault as a silent zero/blank. | grep `except Exception`/`except:`; read each handler. Note: a *deliberate* best-effort fallback (e.g. SMC/IOReport probe that legitimately may be absent) is acceptable **if** it degrades visibly (returns a sentinel the UI renders as `–`/unavailable), not a fake 0. |

`actop/__init__.py` (and `actop/tui/__init__.py`) should be docstring/version-only — a
populated one that does real work at import is an R5/R6 finding.

### actop layer order (for R4 / R5)

From the `CLAUDE.md` architecture table + data flow. A lower layer importing a higher one
(other than at the CLI composition root) is an R4 back-edge candidate:

```
native infra:   ioreport.py, smc.py              (ctypes bindings; import nothing above)
       ↓
sampler:        sampler.py                        (uses ioreport + smc)
       ↓
public API:     api.py  →  models.py              (wraps sampler; maps to SystemSnapshot)
       ↓
TUI:            tui/app.py, tui/widgets.py         (consume api / models)

foundational cross-cutting (importable by any layer, not an inversion):
                utils.py, soc_profiles.py, power_scaling.py, config.py, export.py

composition roots (may import across layers freely):
                actop.py (CLI entry), api.py Monitor construction
```

Back-edge examples that ARE violations: `ioreport`/`smc`/`sampler` importing `tui` or
`api`; `api` importing `tui`; `models` importing anything above it. `config`/`utils`/
`soc_profiles` importing `tui` is also an inversion (foundational must not depend on UI).

---

## Pass 0 — Scope + import graph + cheap sweeps

1. **Resolve scope** from `$ARGUMENTS` (default `actop/`). State it in the summary. For a
   narrow scope (e.g. `actop/tui`) still build the *whole* edge map so cross-boundary
   edges into the scope are visible.

2. **Pick up any open report:** `ls docs/TODO-conformance-*.md`. If a recent one exists
   and is unaddressed, read it — fold new findings in, do not re-list already-tracked
   violations as new.

3. **Build the import edge map.** Paste the script below verbatim into
   `tmp/import_edges.py` (manual audit aid — never a CI gate or `tests/` member) and run
   it. It tags each intra-`actop` edge with its **scope** (`MODULE` / `TYPE_CHECKING` /
   `LOCAL`) and a **PRIVATE** flag (imported module path contains `._x` or an imported
   name starts with `_`). The tags are mandatory: a `TYPE_CHECKING`-only or function-local
   edge is a weak coupling (forward-ref annotation / lazy import), NOT a runtime inversion,
   and must not inflate R4.

   ```python
   # tmp/import_edges.py — manual audit aid; NOT a CI gate or tests/ member
   import ast, sys
   from pathlib import Path

   ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("actop")
   PKG = ROOT.name  # "actop"

   def is_private(name: str) -> bool:
       # single-underscore only; dunders (__version__, __init__, __all__) are public
       return any(
           part.startswith("_") and not (part.startswith("__") and part.endswith("__"))
           for part in name.split(".")
       )

   def walk_imports(tree):
       """Yield (scope, node) for each import node, tagging TYPE_CHECKING / LOCAL."""
       def _walk(nodes, scope):
           for node in nodes:
               if isinstance(node, ast.If):
                   test = node.test
                   if (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                       isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                   ):
                       yield from _walk(node.body, "TYPE_CHECKING")
                       yield from _walk(node.orelse, scope)
                       continue
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                   yield from _walk(node.body, "LOCAL")
                   continue
               if isinstance(node, (ast.Import, ast.ImportFrom)):
                   yield scope, node
               for attr in ("body", "orelse", "finalbody", "handlers"):
                   if hasattr(node, attr):
                       yield from _walk(getattr(node, attr) or [], scope)
       yield from _walk(tree.body, "MODULE")

   for py in sorted(ROOT.rglob("*.py")):
       try:
           tree = ast.parse(py.read_text(encoding="utf-8"))
       except SyntaxError:
           continue
       src = ".".join(py.relative_to(ROOT.parent).with_suffix("").parts)
       for scope, node in walk_imports(tree):
           if isinstance(node, ast.ImportFrom):
               mod = node.module or ""
               if node.level:  # resolve relative import
                   anchor = list(py.relative_to(ROOT.parent).with_suffix("").parts)
                   base = anchor[: -node.level] if node.level <= len(anchor) else []
                   mod = ".".join(base + ([mod] if mod else []))
               if not mod.startswith(PKG):
                   continue
               for a in node.names:
                   # is_private() is dunder-aware — do NOT use a.name.startswith("_")
                   # here, or public dunders like __version__ get mis-tagged PRIVATE.
                   priv = is_private(mod) or is_private(a.name)
                   tag = "PRIVATE" if priv else "PUBLIC"
                   print(f"{src}\t{mod}.{a.name}\t{py}:{node.lineno}\t{scope}\t{tag}")
           elif isinstance(node, ast.Import):
               for a in node.names:
                   if not a.name.startswith(PKG):
                       continue
                   tag = "PRIVATE" if is_private(a.name) else "PUBLIC"
                   print(f"{src}\t{a.name}\t{py}:{node.lineno}\t{scope}\t{tag}")
   ```

   ```bash
   mkdir -p tmp
   .venv/bin/python tmp/import_edges.py > tmp/edges.txt
   ```

   Then:
   - **R4** = a MODULE-scope edge that inverts the layer order above. `TYPE_CHECKING` /
     `LOCAL` edges go in a separate low-priority bucket.
   - **R5** = any cross-package edge (`actop` ↔ `actop.tui`) with `PRIVATE` set. Derive it
     from this map, never from a flat grep (same-package `_x` imports are legal).

4. **Cheap grep/AST sweeps** seed candidate lists — each confirmed by reading source in
   Pass 1, never flagged from the sweep alone:

   ```bash
   rg -n "_legacy|_compat|\b_old\b|\bdeprecated\b" actop/          # R8 (\b_old\b skips locals like t_old)
   rg -n "except Exception|except:" actop/                          # R12 candidates
   rg -n "\bcreated\b *[:=]|\bupdated\b *[:=]" actop/              # R9 timestamp suffix drift
   ruff check actop/ 2>&1 | rg "F401|F811|unused"                  # R10 stale imports
   rg -n "LoadLibrary|CDLL|\bopen\(|getenv|environ\[" actop/       # R6 module-scope IO — confirm NOT under a def/if-guard by reading
   ```

   For **populated `__init__.py`** (R5/R6): use AST, not a line count — a multi-line
   docstring is not code. Flag `actop/__init__.py` or `actop/tui/__init__.py` only if
   `ast.get_docstring()`-stripped body has a non-docstring, non-`__version__`,
   non-`__all__` statement.

   For **dependency drift** (R10): collect third-party top-level imports across `actop/`,
   diff against `[project.dependencies]` in `pyproject.toml` **both ways** — a declared dep
   with no import is *unused*; an imported module with no direct declaration (only pulled in
   transitively, e.g. `rich` via `textual`) is *undeclared*. Resolve dist-name ≠ import-name
   before flagging either direction (a bare diff false-flags such packages).

---

## Pass 1 — Rule-class audit

Read-only. Two ways to run, pick by scope:

- **Whole-`actop/` run (thorough):** optionally fan out to read-only subagents (declared
  tools `Read, Grep, Bash`; no Edit/Write), grouped by **rule cluster** so each holds one
  mental model across the tree:
  - **A — boundaries & visibility:** R4 (consume `tmp/edges.txt`), R5, populated `__init__`.
  - **B — subtraction:** R1, R2, R3, R10.
  - **C — lifecycle & errors:** R6, R7, R12.
  - **D — naming & compat:** R8, R9, R11.
- **Narrow scope or a small pass:** actop is a compact single-package codebase — a single
  read-only sweep by the orchestrator is usually sufficient; reserve the fan-out for a
  full periodic audit.

Each pass returns **only** a findings table — no narrative, no fixes:

| rule | file:line | what (the offending symbol/edge) | evidence (a read, not a grep hit) | proposed source fix |
|------|-----------|----------------------------------|-----------------------------------|---------------------|

**Evidence is mandatory and must be a read.** For R1: cite the write site AND the absent
read site (grep returned 0). For R4: cite the importing line + the layer inversion. For
R5: cite the cross-package `from ..._x import` + the defining module. A row without a
read-confirmed `file:line` is dropped.

R1/R3/R10 systematically **over-flag** (a "one-sided" field is often read via `getattr` /
serialization / a dataclass auto-read; a single-caller wrapper may be a deliberate seam; a
one-implementation Protocol may be a published extension contract). R2/R11 **under-flag**
(judgment-heavy). Treat every DELETE/REMOVE proposal as a *candidate* to re-confirm.

---

## Pass 2 — Dedup, prioritize, write the report

The orchestrator (not a subagent) does this — keeps source-verification in one place.

1. **Merge + dedup.** One physical violation = one row even if it trips two rules (note
   both). **Dead (R10) dominates a lifecycle finding:** before proposing a defer-to-call
   fix for an R6/R7 row, grep the symbol's reader count — zero readers reclassifies it to
   R10 and *deletion supersedes the lifecycle remedy*.

2. **Re-verify each row against source.** Drop any you cannot confirm by reading the cited
   line. **Every DELETE/REMOVE proposal (R1/R3/R10) gets a blind re-read** — re-read the
   cited symbol with the verdict set aside and actively look for any reader / caller / test
   / serialization / re-export that disqualifies "dead". Only survivors enter the report.
   (Empirically this flips a large fraction of removal candidates.) **Also reclassify, don't
   just confirm:** for a write-only field, grep for a hardcoded literal doing its job
   elsewhere — if one exists, it is a *wiring gap* (retitle the finding "decide wire-up vs
   delete", carry a behavior-change flag), not a clean delete. (Real miss this happened on:
   `usage_track_window` / `core_history_window` were "dead" until the deque `maxlen=500`
   hardcode surfaced as their intended consumer.)

3. **Prioritize — do not dump the whole backlog into one report.**
   - **Recurrence class first.** A violation *class* that recurs (R5 underscore leaks ×N,
     R4 back-edges ×N, stale imports ×N) is the highest-value target — fixing it
     structurally (relocate the shared helper) prevents the whole family. Cross-check
     `git log` for the same fix-commit class repeating.
   - **Then severity:** boundary/visibility inversions (R4/R5) and swallowed user-visible
     errors (R12) over cosmetic naming (R9).
   - **Cap the report's action plan** at one coherent refactoring theme; list the rest in a
     `## Deferred backlog` section with counts. An unscoped 100-item plan never ships.

4. **Write `docs/TODO-conformance-YYYY-MM-DD.md`** (see template below). Each action
   task:
   - names the rule + `file:line`,
   - states the **structural** fix (relocate / collapse / delete / rename), not a detection,
   - `done_when` = a behavioral/observable criterion (`.venv/bin/pytest -q` green + the
     back-edge no longer appears in `tmp/edges.txt`), **never** "a guard test passes".
   - Carries a `no behavior change` note where it applies (most subtraction refactors are
     behavior-preserving; verify with the suite + a live `actop` run per `CLAUDE.md`).

5. **If a deletion would orphan a production helper**, chain it into the same task — don't
   leave it for lint to discover.

---

## Report template

```markdown
# TODO — conformance audit (actop) · <date>

Scope: <path>   ·   Import edges scanned: N   ·   Prior report folded: <file|none>

## Inventory (read-confirmed)

| rule | file:line | what | fix |
|------|-----------|------|-----|
| R4 | actop/… | … | relocate … |

## This round — <one coherent theme>

- [ ] **<rule> <file:line>** — <structural fix>. done_when: pytest -q green + <observable>.

## Deferred backlog

- R9 naming drift: N  ·  R11 duplication: N  ·  …
```

---

## Verdict + terminal summary

No PASS/FAIL — the output is an inventory + a report to act on via the normal
branch → PR → `/code-review` → merge flow.

```
## audit-conformance — <date>

Scope: <path>          Import edges scanned: N
Violations found: N total (read-confirmed; M grep candidates dropped on read)
  R1 one-sided member:            N
  R2 redundant state:             N
  R3 wrapper / 1-impl abstraction:N
  R4 wrong module home / back-edge:N   ← [list]
  R5 underscore leak:             N   ← [list pkg._x -> importer]
  R6 import-time side effect:     N
  R7 optimistic flag:             N
  R8 backward-compat residue:     N
  R9 naming drift:                N
  R10 dead code / unused dep:     N
  R11 duplication:                N
  R12 swallowed error:            N

Recurring classes (git-log corroborated): [class — count]
Plan this round: <one coherent theme> — K tasks
Deferred backlog: N violations
Report: docs/TODO-conformance-<date>.md

Next: open a feature branch → fix → PR → /code-review → merge (main is PR-only).
```

**Cadence:** run periodically (not per-PR) — the residue accumulates between runs by
design. Good triggers: `git log` shows the same cleanup-commit class repeating, before a
release, or on a fixed interval.
```
