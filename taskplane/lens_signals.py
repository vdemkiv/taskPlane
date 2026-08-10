"""Lens applicability engine — deterministic per-lens verdicts with evidence.

Realizes the approved Design Contract module ``taskplane/lens_signals.py``
(R-0001, design fingerprint 18bb1c89, approach A): a detector per catalog
lens computes a 0..1 applicability score from three deterministic signal
sources — content (bounded file scans), graph (hub/boundary payload from the
dependency graph), and requirement text (acceptance-criteria keywords) — and
every n/a verdict MUST carry machine-generated negative evidence (fail
closed: an n/a without it raises ValueError, so a silent routing gap can
never masquerade as an honest one).

Verdicts: deep >= 0.6, light >= 0.2, else n/a. Budget: deep set hard-capped
at 8 ranked by score; overflow is DEMOTED to light, never dropped. Floors run
AFTER the budget: security may not be n/a when enforcement/boundary surfaces
are touched; architecture is at least light on any code change.

Fixtures-path discount (R-0006, D-0002): path/content/density hits whose
ONLY support is fixture-class files (path segments fixtures/testdata/goldens
at any depth, or a .golden extension) are re-weighted x0.25 with the
discount named in the evidence string — a re-weighting only (evidence is
never suppressed, n/a semantics and the floors are untouched).

Pure stdlib, Python 3.10+. Deterministic by construction: no wall clock, no
randomness, sorted iteration everywhere, bounded reads (<=64KB/file,
<=200 files). The router (lens.route v2, task t2) calls route_verdicts().
"""

from __future__ import annotations

import fnmatch
import json
import os
import re

from path_roles import change_adds_no_test

# ---------------------------------------------------------------- thresholds

DEEP = 0.6            # score >= DEEP  -> "deep"
LIGHT = 0.2           # score >= LIGHT -> "light"; below -> "n/a"
DEEP_CAP = 8          # hard cap on the deep set (overflow demoted to light)
DEEP_TARGET = (5, 7)  # desired deep band (informational; never manufactured)

MAX_FILE_BYTES = 64 * 1024   # per-file content-scan bound
MAX_FILES = 200              # max files content-scanned per ctx

# signal weights (sum is clamped to 1.0)
W_PATH = 0.35      # a changed path matches the lens's surface globs
W_CONTENT = 0.3    # a content marker fires in a changed file
W_DENSITY = 0.25   # density-style content signal (e.g. user-facing strings)
W_KEYWORD = 0.15   # the requirement text mentions the lens's concern
W_GRAPH = 0.35     # graph flag (hub module / boundary contract in impact)

_HUB_DEPENDENTS = 3   # >= this many direct dependents -> hub signal fires

# ------------------------------------------- fixtures-path discount (D-0002)
#
# Checked-in test fixtures LOOK like the surfaces they imitate (a locale
# file under tests/fixtures/ scores like a real locale file), so fixture-only
# diffs inflated i18n/mobile to deep (D-0002). Path/content/density signal
# hits whose ONLY support is fixture-class files are RE-WEIGHTED x0.25 —
# never suppressed: the evidence line survives and names the discount
# (honesty: the evidence says why the score is low), n/a semantics are
# untouched, and the floors still apply after scoring. A hit with any real
# product-file support keeps full weight.

FIXTURE_DISCOUNT = 0.25
_FIXTURE_SEGMENTS = frozenset({"fixtures", "testdata", "goldens"})
_FIXTURE_EXTENSIONS = (".golden",)
_DISCOUNT_NOTE = f"(fixture-path discount x{FIXTURE_DISCOUNT})"


def is_fixture_path(path: str) -> bool:
    """True when the path is fixture-class: any path segment named
    fixtures/testdata/goldens (at any depth), or a .golden extension."""
    p = str(path).replace(os.sep, "/")
    if any(seg.lower() in _FIXTURE_SEGMENTS for seg in p.split("/") if seg):
        return True
    return p.lower().endswith(_FIXTURE_EXTENSIONS)


# B5 (R-0008): the classifier above keys off the PATH alone, so a REAL
# product directory literally named `fixtures/` was discounted — the
# dangerous direction (under-routing). The discount APPLICATION point now
# consults a graph-informed exemption computed at ctx construction: a
# fixture-classed path whose containing graph MODULE has at least
# FIXTURE_EXEMPT_MIN_DEPENDENTS dependents is real product code (nothing
# depends on a test-fixture tree), keeps FULL weight, and says so in the
# evidence line. The exemption can only ever RESTORE weight — the
# discounted set strictly shrinks, never grows — and is_fixture_path()
# itself is untouched, so every other caller sees the same classification.
#
# TWO guards keep that premise TRUE, because the first cut of this exemption
# repealed D-0002 on this very repository:
#
#  1. WHAT COUNTS AS A DEPENDENT — only depgraph.DEPENDENCY_EDGE_KINDS
#     ("from NEEDS to"). `module_dependents` used to count EVERY incoming
#     edge, including the STRUCTURAL `defined_in` edge a docker-compose file
#     emits ABOUT ITS OWN module. This repo's
#     taskplane/tests/fixtures/detectors/architecture/positive/
#     docker-compose.yml therefore handed module `taskplane/tests` two
#     "dependents" that were the fixtures themselves. Filtered in
#     _graph_payload here AND in decompose._graph_payload, so cached
#     component maps and live routing agree.
#
#  2. AT WHAT GRANULARITY — a graph module id is at most a few segments
#     (`taskplane/tests`) while is_fixture_path classifies the FULL path
#     (`taskplane/tests/fixtures/detectors/i18n/positive/locales/en.json`).
#     An ancestor module id must never speak for a fixture subtree nested
#     below it, so the exemption fires only when the fixture classification
#     is visible AT the module boundary that carries the dependent count —
#     is_fixture_path(module) is itself True (`fixtures`, `tests/fixtures`,
#     …). When the fixture-marking segment lies BELOW the module id, the
#     dependents belong to the module, not to the fixture tree, and the
#     discount stands. On this repo all 102 fixture-classed tracked paths
#     map to non-fixture-classed modules (taskplane/tests, src, api,
#     components, auth), so the whole corpus stays discounted.
#
# Both guards only SHRINK the exemption, and the exemption only ever restores
# weight, so behaviour stays between "always discounted" (base) and the
# unguarded version: it can never route anything more narrowly than base.
FIXTURE_EXEMPT_MIN_DEPENDENTS = 1


def _module_is_fixture_classed(module: str) -> bool:
    """Guard 2: the fixture marking must be visible at the module boundary
    that carries the dependent count, not somewhere deeper in the path."""
    m = str(module or "").replace(os.sep, "/").strip("/")
    return bool(m) and m != "(root)" and is_fixture_path(m)


def _module_of(path: str) -> str:
    """The graph module id owning `path` (depgraph's own rule). A degraded
    fallback keeps ctx construction non-raising without depgraph."""
    p = str(path).replace(os.sep, "/")
    try:
        import depgraph
        return depgraph.module_of(p)
    except Exception:
        d = os.path.dirname(p)
        return "/".join(d.split("/")[:2]) if d else "(root)"


def _fixture_exemptions(files, graph) -> dict:
    """{path: reason} — the fixture-classed paths that keep FULL weight.

    Guard 2 (granularity) is applied here; guard 1 (dependency-edge kinds)
    is applied where `module_dependents` is BUILT (_graph_payload)."""
    deps = (graph or {}).get("module_dependents")
    if not isinstance(deps, dict) or not deps:
        return {}
    out: dict = {}
    for rel in files:
        if not is_fixture_path(rel):
            continue
        module = _module_of(rel)
        if not _module_is_fixture_classed(module):
            # the fixture segment sits BELOW the module boundary: this
            # module's dependents do not describe the fixture tree
            continue
        try:
            n = int(deps.get(module, 0) or 0)
        except (TypeError, ValueError):
            n = 0
        if n >= FIXTURE_EXEMPT_MIN_DEPENDENTS:
            out[rel] = (f"product-dir exemption: {rel} is in module "
                        f"{module}, which has {n} dependent(s) — real "
                        "product code, not a test fixture")
    return out


# ------------------------------------------------------------------- catalog

_CATALOG_CACHE: dict = {}


def _plugin_root() -> str:
    # lenses/ sits at the plugin root, one level up from taskplane/
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_catalog(root: str | None = None) -> dict:
    """Load lenses/catalog.json (cached per root). Self-contained on purpose:
    lens.py will import THIS module in t2, so importing lens here would set
    up an import cycle."""
    key = root or _plugin_root()
    if key not in _CATALOG_CACHE:
        with open(os.path.join(key, "lenses", "catalog.json")) as f:
            _CATALOG_CACHE[key] = json.load(f)
    return _CATALOG_CACHE[key]


# -------------------------------------------------------------- glob matcher

def _match(path: str, glob: str) -> bool:
    """Path/glob match supporting '**' as 'any directories' (same semantics
    as lens._match; duplicated to avoid the lens<->lens_signals cycle)."""
    if fnmatch.fnmatch(path, glob):
        return True
    if glob.startswith("**/"):
        tail = glob[3:]
        if fnmatch.fnmatch(path, tail) or fnmatch.fnmatch(
                os.path.basename(path), tail):
            return True
        parts = path.split("/")
        for i in range(len(parts)):
            if fnmatch.fnmatch("/".join(parts[i:]), tail):
                return True
    return False


def _glob_hit(files, globs):
    """First (file, glob) pair that matches, or None. files/globs iterated
    in the given (already sorted) order -> deterministic."""
    for g in globs:
        for f in files:
            if _match(f, g):
                return (f, g)
    return None


def _is_code(path: str, code_ext) -> bool:
    return any(path.endswith(e) for e in code_ext)


# ------------------------------------------------------------------- context

class Ctx:
    """Everything a detector may look at. Bounded, cached, deterministic.

    files            changed paths relative to workspace (sorted, deduped)
    workspace        absolute-ish root used to read file contents
    requirement_text lowercased requirement/acceptance-criteria blob
    graph            {"hub_dependents": int, "boundary_contracts": [str],
                      "modules": [str], "module_dependents": {mod: int}}
    stage            loop stage (carried for the router; unused by detectors)
    fixture_exempt   {path: reason} — fixture-classed paths that keep FULL
                     weight because their graph module has dependents (B5,
                     R-0008); computed HERE, at ctx construction
    """

    __slots__ = ("workspace", "files", "requirement_text", "graph", "stage",
                 "fixture_exempt", "_contents")

    def __init__(self, workspace, files, requirement_text, graph, stage):
        self.workspace = workspace
        self.files = sorted({str(f).replace(os.sep, "/") for f in files or []})
        if isinstance(requirement_text, (list, tuple)):
            requirement_text = "\n".join(str(x) for x in requirement_text)
        self.requirement_text = (requirement_text or "").lower()
        self.graph = graph or {"hub_dependents": 0,
                               "boundary_contracts": [], "modules": []}
        self.stage = stage
        self.fixture_exempt = _fixture_exemptions(self.files, self.graph)
        self._contents = None

    def is_discounted(self, path: str) -> bool:
        """True when the D-0002 fixture discount APPLIES to `path`: it is
        fixture-class AND not graph-exempt (B5). A strict subset of
        is_fixture_path() — the exemption can only restore weight."""
        p = str(path).replace(os.sep, "/")
        return is_fixture_path(p) and p not in self.fixture_exempt

    def exemption_note(self, path: str) -> str:
        """' (reason)' when `path` is exempt from the discount, else ''."""
        reason = self.fixture_exempt.get(str(path).replace(os.sep, "/"))
        return f" ({reason})" if reason else ""

    def read(self, relpath: str) -> str | None:
        """Bounded read of one changed file: at most MAX_FILE_BYTES bytes,
        decoded utf-8 with replacement. Missing/unreadable -> None (changed
        lists legitimately contain deletions)."""
        p = os.path.join(self.workspace, relpath)
        try:
            # Containment (EM v3): changed-file lists come from git, but a
            # crafted relpath ('../..') or a symlink pointing outside the
            # workspace must not let a detector read foreign files. Resolve
            # and require the real target stays under the real workspace.
            root = os.path.realpath(self.workspace)
            real = os.path.realpath(p)
            if real != root and not real.startswith(root + os.sep):
                return None
            with open(real, "rb") as f:
                return f.read(MAX_FILE_BYTES).decode("utf-8", "replace")
        except OSError:
            return None

    def contents(self):
        """[(relpath, text)] for the first MAX_FILES sorted changed files
        that exist. Cached: the corpus is read once per ctx, then every
        detector scans the same in-memory snapshot."""
        if self._contents is None:
            out = []
            for rel in self.files:
                if len(out) >= MAX_FILES:
                    break
                text = self.read(rel)
                if text is not None:
                    out.append((rel, text))
            self._contents = out
        return self._contents


def make_ctx(workspace, files, requirement_text=None, graph=None,
             stage=None) -> Ctx:
    """Build a detector context. graph=None -> derive a payload from the
    dependency graph (hub dependents + boundary contracts adjacent to the
    touched modules); any graph failure degrades to an empty payload rather
    than blocking routing (the security floor still fires on path evidence,
    and route v2 fails open to breadth=all at the router seam — t2)."""
    if graph is None:
        graph = _graph_payload(workspace, files)
    return Ctx(workspace, files, requirement_text, graph, stage)


def _graph_payload(workspace, files) -> dict:
    try:
        import depgraph
        g = depgraph.load(workspace)
        touched = sorted({depgraph.module_of(str(f).replace(os.sep, "/"))
                          for f in files or []})
        rev: dict[str, set] = {}
        dep_rev: dict[str, set] = {}
        adjacent_contracts: set = set()
        tset = set(touched)
        for e in g.get("edges") or []:
            frm, to = e.get("from"), e.get("to")
            if not frm or not to:
                continue
            rev.setdefault(to, set()).add(frm)
            if depgraph.is_dependency_edge(e):
                dep_rev.setdefault(to, set()).add(frm)
            for a, b in ((frm, to), (to, frm)):
                if a in tset and str(b).startswith("contract:"):
                    adjacent_contracts.add(b)
        # hub_dependents keeps counting EVERY incoming edge — it is the
        # pre-existing "is this a hub?" signal and narrowing it would drop
        # graph weight off lenses that earn it today.
        hub = max((len(rev.get(m, ())) for m in touched), default=0)
        # module_dependents feeds the B5 fixture-path exemption, which asks a
        # much stricter question — "does real code DEPEND on this?" — so it
        # counts only depgraph.DEPENDENCY_EDGE_KINDS. Structural kinds
        # (defined_in, planned/realizes, provides, validates, changes) are
        # excluded: a fixture's own docker-compose.yml must not be able to
        # vouch for the fixture tree it lives in. Mirrored exactly in
        # decompose._graph_payload so cached maps and live routing agree.
        #
        # GUARD 3 (EM, v3 phase 3): the dependent must not ITSELF be
        # fixture-classed. Guards 1 and 2 ask what KIND of edge and at what
        # GRANULARITY; neither asked WHO is vouching — so one test tree
        # importing another (testdata/core → fixtures/core) exempted the
        # second and a fixture-only diff kept full weight. That is exactly
        # the D-0002 routing inflation the discount exists to prevent. A
        # fixture cannot be its own witness.
        return {"hub_dependents": hub,
                "boundary_contracts": sorted(adjacent_contracts),
                "modules": touched,
                "module_dependents": {
                    m: len([d for d in (dep_rev.get(m) or ()) if d != m
                            and not _module_is_fixture_classed(d)])
                    for m in touched}}
    except Exception:
        return {"hub_dependents": 0, "boundary_contracts": [], "modules": [],
                "module_dependents": {}}


# --------------------------------------------------------- signal-spec table
#
# One spec per catalog lens. Shape:
#   paths:    extra surface globs ON TOP of the lens's catalog globs
#             (the catalog globs are always part of the path signal)
#   code:     True -> the path signal also fires on any code-extension file
#             (baseline lenses: their surface IS "code changed")
#   content:  [(label, regex_pattern)] scanned over the bounded corpus;
#             each distinct rule that fires adds W_CONTENT
#   density:  (label, threshold) -> user-facing string-literal density rule
#   graph:    subset of {"hub", "boundary"}
#   keywords: substrings looked up in the lowercased requirement text
#   absent:   negative-evidence phrases, joined into
#             "0 <lens> signals: no X, no Y, ..." when nothing fires

_STR_LIT = re.compile(r"""["']([A-Za-z][A-Za-z,.!?'-]*(?:\s+[A-Za-z][A-Za-z,.!?'-]*){2,})["']""")

SPECS: dict[str, dict] = {
    "product": {
        "content": [("spec/acceptance markers",
                     r"(?im)^#+.*(acceptance criteria|user stor|requirement)")],
        "keywords": ["user journey", "acceptance", "success metric",
                     "user value"],
        "absent": ["no spec/requirements files", "no acceptance-criteria "
                   "markers", "no product keywords in the requirement"],
    },
    "security": {
        "paths": ["**/hooks/**", "**/taskplane_lite.py", "**/*login*",
                  "**/*permission*", "**/*.pem", "**/*credential*"],
        "content": [
            ("auth/secret markers",
             r"(?i)(password|passwd|secret[_a-z]*\s*=|api[_-]?key|"
             r"bearer\s|jwt|oauth|csrf|bcrypt|hmac|authenticat|authoriz|"
             r"permission|session[_ ]token)"),
            ("unsafe-input surface",
             r"(?i)(\beval\(|\bexec\(|subprocess|os\.system|pickle\.loads|"
             r"yaml\.load\(|innerHTML|dangerouslySetInnerHTML|"
             r"shell\s*=\s*True)"),
        ],
        "graph": ["boundary"],
        "keywords": ["auth", "security", "secret", "permission", "vulnerab",
                     "enforc", "injection"],
        "absent": ["no auth/secrets/enforcement paths", "no auth or secret "
                   "markers", "no unsafe-input surface", "no boundary "
                   "contracts in impact", "no security keywords in the "
                   "requirement"],
    },
    "code-quality": {
        "code": True,
        "content": [("code constructs",
                     r"(?m)^\s*(def |class |function\b|const |public |"
                     r"private |fn |func )")],
        "absent": ["no code files changed", "no code constructs in scope"],
    },
    "testability": {
        "code": True,
        "paths": ["**/tests/**", "**/*.test.*", "**/conftest*"],
        "content": [("non-determinism/seam markers",
                     r"(?i)(time\.time|datetime\.now|random\.|monkeypatch|"
                     r"\bmock|singleton|\bglobal )")],
        "keywords": ["testab", "coverage", "determinis", "mockab"],
        "absent": ["no code files changed", "no test files", "no seam or "
                   "non-determinism markers"],
    },
    "design": {
        "content": [("UI markup",
                     r"(?m)(<[A-Za-z][^>\n]*>|className=|class=\"|"
                     r"<template|styled\.)")],
        "keywords": ["ux", "visual", "layout", "empty state", "loading state"],
        "absent": ["no UI component files", "no UI markup",
                   "no UX keywords in the requirement"],
    },
    "scalability": {
        "content": [
            ("SQL query surface",
             r"(?i)(select\s+.+\s+from|insert\s+into|\bjoin\b|group\s+by)"),
            ("HTTP/queue clients",
             r"(?i)(requests\.(get|post|put|delete)|urllib\.request|"
             r"\bfetch\(|axios|http\.client|aiohttp|kafka|rabbitmq|\bsqs\b|"
             r"pub/?sub|celery)"),
            ("loops over remote calls",
             r"(?is)\b(for|while)\b[^\n]*\n[^\n]{0,200}?"
             r"(select\s|\.execute\(|requests\.|fetch\(|\.query\()"),
        ],
        "graph": ["hub"],
        "keywords": ["scale", "scalab", "throughput", "latency", "hot path",
                     "load"],
        "absent": ["no api/db/services paths", "no query or client code",
                   "no remote calls in loops", "no hub module in the graph",
                   "no scalability keywords in the requirement"],
    },
    "integrability": {
        "content": [("contract/schema markers",
                     r"(?i)(openapi|swagger|protobuf|proto3|json[- ]?schema|"
                     r"content-type|api[_-]?version|/v[0-9]+/)")],
        "graph": ["boundary"],
        "keywords": ["contract", "api version", "integrat", "error code"],
        "absent": ["no api/schema/contract paths", "no contract or schema "
                   "markers", "no boundary contracts in impact"],
    },
    "data-safety": {
        "content": [("migration/DDL markers",
                     r"(?i)(alter\s+table|drop\s+(table|column)|"
                     r"add\s+column|backfill|\bmigration|not\s+null|"
                     r"on\s+delete\s+cascade)")],
        "keywords": ["migration", "rollback", "backfill", "cascade"],
        "absent": ["no migration/schema files", "no DDL or backfill markers",
                   "no migration keywords in the requirement"],
    },
    "tech-writer": {
        "content": [("doc structure",
                     r"(?m)^#{1,3}\s|^\.\. |^=====")],
        "keywords": ["readme", "changelog", "documentation", "adr"],
        "absent": ["no docs/markdown files", "no document structure",
                   "no documentation keywords in the requirement"],
    },
    "qa": {
        "content": [("test constructs",
                     r"(?m)(\bassert\b|expect\(|\bit\(|describe\(|"
                     r"@pytest|unittest)")],
        "keywords": ["regression", "edge case", "e2e", "test strategy"],
        "absent": ["no test files", "no test constructs",
                   "no QA keywords in the requirement"],
    },
    "devops": {
        "content": [("pipeline/build markers",
                     r"(?im)^(FROM |RUN |jobs:|steps:|stages:|pipeline\b|"
                     r"\s+uses:\s)")],
        "keywords": ["pipeline", "ci/cd", "deploy", "reproducib", "iac"],
        "absent": ["no CI/container/IaC files", "no pipeline or build "
                   "markers", "no devops keywords in the requirement"],
    },
    "dba": {
        "content": [
            ("DDL/index markers",
             r"(?i)(create\s+(table|index|unique\s+index)|alter\s+table|"
             r"foreign\s+key|primary\s+key|partition\s+by)"),
            ("query patterns",
             r"(?i)(select\s+.+\s+from|\bjoin\s|group\s+by|order\s+by)"),
            ("ORM/model markers",
             r"(?i)(models\.Model|@Entity|prisma|ActiveRecord|sqlalchemy|"
             r"@Table)"),
        ],
        "keywords": ["index", "query plan", "schema", "normaliz",
                     "partition"],
        "absent": ["no sql/models/schema files", "no DDL or index markers",
                   "no query patterns", "no ORM models"],
    },
    "sre": {
        "content": [("observability/resilience markers",
                     r"(?i)(retry|timeout|circuit[ _-]?breaker|backoff|"
                     r"prometheus|\balert|\bslo\b|healthcheck|"
                     r"health[_ ]check|runbook|pagerduty)")],
        "keywords": ["observab", "alert", "incident", "reliab", "slo",
                     "on-call"],
        "absent": ["no monitoring/alerts/runbook files", "no observability "
                   "or resilience markers", "no SRE keywords in the "
                   "requirement"],
    },
    "project-management": {
        "content": [("plan/rollout structure",
                     r"(?im)^(##\s*(milestone|timeline|rollout|risk|wave)|"
                     r"- \[ \])")],
        "keywords": ["timeline", "milestone", "cross-team", "rollout plan"],
        "absent": ["no plan/roadmap files", "no milestone or rollout "
                   "structure", "no delivery keywords in the requirement"],
    },
    "frontend": {
        "content": [
            ("component markup",
             r"(<[A-Z][A-Za-z0-9]*[\s/>]|className=|useState|useEffect|"
             r"v-if=|@Component)"),
            ("state management",
             r"(?i)(redux|zustand|vuex|pinia|useReducer|createStore)"),
            ("render/bundle perf",
             r"(?i)(React\.lazy|import\(|\bmemo\(|debounce|"
             r"requestAnimationFrame)"),
        ],
        "keywords": ["frontend", "component", "browser", "bundle"],
        "absent": ["no frontend files", "no component markup",
                   "no state management", "no render/bundle-perf markers"],
    },
    "backend": {
        "content": [
            ("route/handler markers",
             r"(?i)(@app\.(get|post|put|delete)|@router\.|app\.(get|post)\(|"
             r"HandleFunc|express\(\)|def\s+handle_)"),
            ("transaction/idempotency markers",
             r"(?i)(transaction|idempoten|\brollback\b|commit\(\)|"
             r"exactly[- ]once)"),
            ("concurrency primitives",
             r"(?i)(threading\.|asyncio|multiprocessing|semaphore|mutex|"
             r"\block\(\)|async\s+def|goroutine|sync\.WaitGroup)"),
        ],
        "graph": ["boundary"],
        "keywords": ["endpoint", "service boundar", "idempoten",
                     "business logic", "backend"],
        "absent": ["no api/services/handlers paths", "no route handlers",
                   "no transaction or idempotency markers",
                   "no concurrency primitives"],
    },
    "tradeoffs": {
        "content": [("alternatives/decision markers",
                     r"(?i)(trade[- ]?off|alternative|option [ab]\b|"
                     r"\bpros\b|\bcons\b|revisit (if|when)|we chose)")],
        "keywords": ["tradeoff", "trade-off", "alternative", "hidden cost"],
        "absent": ["no adr/design/plan files", "no alternatives or decision "
                   "markers", "no trade-off keywords in the requirement"],
    },
    "solution-design": {
        "content": [("design-contract markers",
                     r"(?i)(design contract|module boundar|"
                     r"proposed (module|graph|edge)|contract ownership|"
                     r"component diagram)")],
        "keywords": ["solution design", "design contract", "module boundar"],
        "absent": ["no design/ files", "no design-contract markers",
                   "no solution-design keywords in the requirement"],
    },
    "services-selection": {
        "content": [("dependency-manifest markers",
                     r"(?i)(\"dependencies\"|install_requires|"
                     r"\[dependencies\]|\brequire\s+['\"]|implementation\s|"
                     r"new (service|vendor|dependency))")],
        "keywords": ["vendor", "lock-in", "self-host", "managed service",
                     "new dependency"],
        "absent": ["no dependency manifests", "no dependency additions",
                   "no selection keywords in the requirement"],
    },
    "time-to-market": {
        "content": [("scope/phasing markers",
                     r"(?i)(\bmvp\b|phase [0-9]|defer(red)?\b|"
                     r"critical path|cut scope|later release)")],
        "keywords": ["deadline", "mvp", "time to market", "defer", "launch"],
        "absent": ["no plan/spec files", "no scope or phasing markers",
                   "no time-to-market keywords in the requirement"],
    },
    "architecture": {
        "content": [
            ("infra topology",
             r"(?im)^(services:|apiVersion:|resource\s+\"|module\s+\")"),
            ("architecture docs",
             r"(?i)(\badr\b|architecture|\bc4\b|component diagram|"
             r"data flow|coupling)"),
            ("service-boundary code",
             r"(?i)(grpc|proto3|message\s+\w+\s*\{|event bus|pub/?sub|"
             r"\bqueue\b)"),
        ],
        "graph": ["hub", "boundary"],
        "keywords": ["architect", "decompos", "coupling", "consistency",
                     "boundar"],
        "absent": ["no architecture/adr/infra files", "no infra topology",
                   "no architecture docs", "no service-boundary code",
                   "no hub module or boundary contract in the graph"],
    },
    "mobile": {
        "content": [
            ("platform APIs",
             r"(?i)(UIKit|SwiftUI|UIViewController|UIApplication|"
             r"android\.(os|app|content)|\bActivity\b|\bFragment\b|"
             r"\bIntent\b|CoreData|WorkManager)"),
            ("lifecycle/permissions",
             r"(?i)(onCreate|onResume|viewDidLoad|requestPermissions|"
             r"uses-permission|NSLocationWhenInUse|Info\.plist)"),
            ("offline/battery",
             r"(?i)(offline|sync adapter|battery|\bdoze\b|reachability)"),
        ],
        "keywords": ["ios", "android", "mobile", "offline", "app store"],
        "absent": ["no ios/android files", "no platform APIs",
                   "no lifecycle or permission markers",
                   "no offline/battery markers"],
    },
    "accessibility": {
        "content": [("a11y markers",
                     r"(?i)(aria-[a-z]+|role=|alt=|tabindex|screen reader|"
                     r"wcag|focus management|contrast)")],
        "keywords": ["accessib", "wcag", "aria", "keyboard nav"],
        "absent": ["no UI files", "no ARIA/alt/focus markers",
                   "no accessibility keywords in the requirement"],
    },
    "privacy-compliance": {
        "content": [("PII/consent markers",
                     r"(?i)(\bpii\b|gdpr|ccpa|consent|personal data|"
                     r"data retention|anonymi[sz]|email[_ ]address|"
                     r"\btracking\b|\banalytics\b)")],
        "keywords": ["privacy", "pii", "gdpr", "consent", "retention"],
        "absent": ["no privacy/analytics/consent paths", "no PII or consent "
                   "markers", "no privacy keywords in the requirement"],
    },
    "cost-finops": {
        "content": [("provisioning/cost markers",
                     r"(?im)(instance_type|autoscal|reserved|\begress\b|"
                     r"provisioned|^\s*(cpu|memory):\s|replicas:)")],
        "keywords": ["cost", "spend", "finops", "over-provision", "budget"],
        "absent": ["no IaC/k8s files", "no provisioning or cost markers",
                   "no cost keywords in the requirement"],
    },
    "i18n": {
        "content": [
            ("i18n imports",
             r"(?i)(import\s+[^\n]*i18n|require\(['\"](i18n|i18next)|"
             r"react-intl|formatjs|\bgettext\b|ngettext|from\s+['\"]i18n)"),
            ("locale data",
             r"(?i)(\"locale\"|\blang=|LC_ALL|setlocale|\bmsgid\b|"
             r"pluraliz|\brtl\b)"),
        ],
        "density": ("user-facing string literals", 5),
        "keywords": ["i18n", "locale", "translat", "localiz", "rtl"],
        "absent": ["no locale files", "no i18n imports",
                   "no user-facing string literals in scope"],
    },
}


# ------------------------------------------------------------ detector build

def _compiled(spec: dict):
    """Compile a spec's content rules once (cached on the spec dict)."""
    key = "_compiled"
    if key not in spec:
        spec[key] = [(label, re.compile(pat))
                     for label, pat in spec.get("content", ())]
    return spec[key]


def _density_hits(ctx: Ctx, code_ext) -> tuple[int, int, int, str]:
    """(count, files, real_count, real_rel) of user-facing-looking string
    literals (>= 3 words) across changed code files; real_count counts only
    files the D-0002 discount does NOT apply to (fixture-class and not
    B5-exempt), and real_rel names the first such file."""
    total, nfiles, real, real_rel = 0, 0, 0, ""
    for rel, text in ctx.contents():
        if not _is_code(rel, code_ext):
            continue
        n = len(_STR_LIT.findall(text))
        if n:
            total += n
            nfiles += 1
            if not ctx.is_discounted(rel):
                real += n
                real_rel = real_rel or rel
    return total, nfiles, real, real_rel


def _spec_detect(lens_id: str, spec: dict, catalog_lens: dict,
                 cat: dict, ctx: Ctx) -> dict:
    evidence = []
    score = 0.0

    # -- path signal: catalog globs + spec extras (+ code extensions when
    #    the lens's surface is "any code change")
    globs = sorted(set((catalog_lens.get("globs") or [])
                       + list(spec.get("paths", ()))))
    # `is_discounted` is `is_fixture_path` minus the B5 product-dir
    # exemption, so full-weight support is a SUPERSET of what it was.
    real_files = [f for f in ctx.files if not ctx.is_discounted(f)]
    hit = _glob_hit(ctx.files, globs) if globs else None
    if hit:
        real_hit = _glob_hit(real_files, globs)
        if real_hit:
            evidence.append(f"path: {hit[0]} matches {hit[1]}"
                            + ctx.exemption_note(real_hit[0]))
            score += W_PATH
        else:   # ONLY fixture-class support -> re-weight, never suppress
            evidence.append(f"path: {hit[0]} matches {hit[1]} "
                            f"{_DISCOUNT_NOTE}")
            score += W_PATH * FIXTURE_DISCOUNT
    elif spec.get("code"):
        code_ext = cat.get("code_extensions") or []
        code_files = [f for f in ctx.files if _is_code(f, code_ext)]
        if code_files:
            label = (f"path: code change ({code_files[0]}"
                     + (f" +{len(code_files) - 1} more" if
                        len(code_files) > 1 else "") + ")")
            real_code = [f for f in code_files if not ctx.is_discounted(f)]
            if real_code:
                evidence.append(label + ctx.exemption_note(real_code[0]))
                score += W_PATH
            else:
                evidence.append(f"{label} {_DISCOUNT_NOTE}")
                score += W_PATH * FIXTURE_DISCOUNT

    # Lenses 2.0: absence can itself be applicability evidence. QA must see
    # a production-code change that carries no test path; adding the reason
    # only in lens.route was too late because this engine had already
    # returned n/a. Give the trigger normal path-signal weight so it routes
    # light without manufacturing a deep verdict.
    if (catalog_lens.get("untested_trigger")
            and change_adds_no_test(ctx.files,
                                    cat.get("code_extensions") or [])):
        evidence.append("change shape: code changed with no test file")
        score += W_PATH

    # -- content signals (bounded corpus, first hit per rule)
    for label, rx in _compiled(spec):
        found = None
        real_rel = None
        for rel, text in ctx.contents():
            if rx.search(text):
                if found is None:
                    found = rel
                if not ctx.is_discounted(rel):
                    real_rel = rel
                    break
        if found:
            if real_rel is not None:
                evidence.append(f"content: {label} in {found}"
                                + ctx.exemption_note(real_rel))
                score += W_CONTENT
            else:   # ONLY fixture-class support
                evidence.append(f"content: {label} in {found} "
                                f"{_DISCOUNT_NOTE}")
                score += W_CONTENT * FIXTURE_DISCOUNT

    # -- density signal
    dens = spec.get("density")
    if dens:
        label, threshold = dens
        count, nfiles, real_count, real_rel = _density_hits(
            ctx, cat.get("code_extensions") or [])
        if count >= threshold:
            if real_count:
                evidence.append(f"content: {label}: {count} across "
                                f"{nfiles} file(s)"
                                + ctx.exemption_note(real_rel))
                score += W_DENSITY
            else:   # ONLY fixture-class support
                evidence.append(f"content: {label}: {count} across "
                                f"{nfiles} file(s) {_DISCOUNT_NOTE}")
                score += W_DENSITY * FIXTURE_DISCOUNT

    # -- requirement-text keywords
    kws = sorted(k for k in spec.get("keywords", ())
                 if k in ctx.requirement_text)
    if kws:
        evidence.append("requirement: mentions " + ", ".join(kws))
        score += W_KEYWORD

    # -- graph flags
    for flag in spec.get("graph", ()):
        if flag == "hub":
            hub = int(ctx.graph.get("hub_dependents") or 0)
            if hub >= _HUB_DEPENDENTS:
                evidence.append(f"graph: hub module ({hub} direct "
                                "dependents)")
                score += W_GRAPH
        elif flag == "boundary":
            bcs = sorted(ctx.graph.get("boundary_contracts") or [])
            if bcs:
                evidence.append("graph: boundary contracts in impact: "
                                + ", ".join(bcs[:3]))
                score += W_GRAPH

    score = round(min(1.0, score), 4)
    negative = []
    if score < LIGHT:
        if evidence:
            negative = [f"0 {lens_id} signals strong enough (score {score} "
                        f"< {LIGHT}): only {len(evidence)} weak indicator(s); "
                        + ", ".join(spec["absent"][:2])]
        else:
            negative = [f"0 {lens_id} signals: "
                        + ", ".join(spec["absent"])]
    return {"score": score, "evidence": evidence,
            "negative_evidence": negative}


def _make_detector(lens_id: str, spec: dict, catalog_lens: dict, cat: dict):
    def detector(ctx: Ctx) -> dict:
        return _spec_detect(lens_id, spec, catalog_lens, cat, ctx)
    detector.__name__ = f"detect_{lens_id.replace('-', '_')}"
    return detector


def _build_registry() -> dict:
    cat = load_catalog()
    by_id = {l["id"]: l for l in cat["lenses"]}
    missing = sorted(set(by_id) - set(SPECS))
    extra = sorted(set(SPECS) - set(by_id))
    if missing or extra:
        # fail closed at import: a catalog/spec drift must never silently
        # route a lens with no detector (or a detector with no lens)
        raise ValueError(f"lens_signals spec drift: missing={missing} "
                         f"extra={extra}")
    return {lid: _make_detector(lid, SPECS[lid], by_id[lid], cat)
            for lid in sorted(by_id)}


DETECTORS: dict = _build_registry()


def requirement_keyword_lenses(ctx: Ctx, lens_ids=None) -> dict:
    """The requirement-keyword detector, re-runnable on its own.

    {lens_id: [matched keywords]} for every lens whose spec keywords appear
    in ctx.requirement_text — the SAME `k in ctx.requirement_text` rule
    `_spec_detect` scores with W_KEYWORD, isolated so a caller holding only
    a ctx can ask "which lenses does this requirement's own text earn?".

    B4 (R-0008): a component's cached lens_map is derived WITHOUT
    requirement_text, so lens.py's component assembly re-runs this LIVE on
    the ctx it already builds and UNIONS the result into the proposed set —
    a cached map may add candidates, never subtract them. Empty
    requirement text -> {} (no widening, byte-unchanged routing)."""
    if not ctx.requirement_text:
        return {}
    ids = sorted(SPECS) if lens_ids is None else \
        sorted(set(lens_ids) & set(SPECS))
    out: dict = {}
    for lid in ids:
        kws = sorted(k for k in SPECS[lid].get("keywords", ())
                     if k in ctx.requirement_text)
        if kws:
            out[lid] = kws
    return out


# ----------------------------------------------------------------- verdicts

def detect(lens_id: str, ctx: Ctx) -> dict:
    """Run one registered detector and validate its result shape.
    Unknown lens or malformed result -> ValueError (fail closed)."""
    det = DETECTORS.get(lens_id)
    if det is None:
        raise ValueError(f"unknown lens id: {lens_id!r} (catalog has "
                         f"{len(DETECTORS)} registered detectors)")
    r = det(ctx)
    if not isinstance(r, dict):
        raise ValueError(f"detector {lens_id}: result must be a dict")
    try:
        score = float(r["score"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"detector {lens_id}: missing/non-numeric score")
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"detector {lens_id}: score {score} outside 0..1")
    ev = r.get("evidence")
    neg = r.get("negative_evidence")
    if not isinstance(ev, list) or not isinstance(neg, list) \
            or not all(isinstance(x, str) for x in ev + neg):
        raise ValueError(f"detector {lens_id}: evidence/negative_evidence "
                         "must be lists of strings")
    return {"score": score, "evidence": list(ev), "negative_evidence":
            list(neg)}


def verdict_for_score(score: float) -> str:
    if score >= DEEP:
        return "deep"
    if score >= LIGHT:
        return "light"
    return "n/a"


def verdicts(lens_ids, ctx: Ctx, floors: bool = True) -> dict:
    """{lens_id: {verdict, score, evidence, negative_evidence}} for the given
    lenses. An n/a WITHOUT negative evidence raises ValueError — an
    unevidenced routing gap must halt the route, not silently skip a lens."""
    out = {}
    for lid in sorted(set(lens_ids)):
        r = detect(lid, ctx)
        v = verdict_for_score(r["score"])
        if v == "n/a" and not r["negative_evidence"]:
            raise ValueError(
                f"lens {lid}: n/a verdict without negative evidence — "
                "detectors must prove absence, not assert it (fail closed)")
        out[lid] = {"verdict": v, "score": r["score"],
                    "evidence": r["evidence"],
                    "negative_evidence": r["negative_evidence"]}
    if floors:
        _apply_floors(out, ctx)
    return out


# ------------------------------------------------------------------- floors

_ENFORCEMENT_MARKERS = ("taskplane_lite.py",)
_ENFORCEMENT_SEGMENTS = ("hooks",)
_AUTHISH = ("auth", "login", "permission", "secret", "credential", "token")


def _security_floor_reason(ctx: Ctx) -> str | None:
    """Non-None when the change touches enforcement/boundary surfaces:
    taskplane_lite.py, hooks/, auth-ish files, or boundary contracts in the
    graph impact."""
    for f in ctx.files:
        base = os.path.basename(f)
        if base in _ENFORCEMENT_MARKERS:
            return f"enforcement surface touched: {f}"
        segs = f.lower().split("/")
        if any(s in segs for s in _ENFORCEMENT_SEGMENTS):
            return f"enforcement surface touched: {f}"
        if any(a in base.lower() for a in _AUTHISH) or f.lower().endswith(
                (".env", ".pem")) or "/.env" in f.lower():
            return f"auth-ish surface touched: {f}"
    bcs = sorted(ctx.graph.get("boundary_contracts") or [])
    if bcs:
        return "boundary contracts in impact: " + ", ".join(bcs[:3])
    return None


def _code_change_file(ctx: Ctx) -> str | None:
    code_ext = load_catalog().get("code_extensions") or []
    for f in ctx.files:
        if _is_code(f, code_ext):
            return f
    return None


def _promote(entry: dict, reason: str) -> None:
    if entry["verdict"] == "n/a":
        entry["verdict"] = "light"
    entry["evidence"] = entry["evidence"] + [reason]
    entry["floor"] = reason


def _apply_floors(vmap: dict, ctx: Ctx) -> dict:
    """Idempotent floors (mutate vmap in place, return it): security may not
    be n/a on enforcement/boundary diffs; architecture is at least light on
    any code change. Reasons are recorded in evidence and under 'floor'."""
    sec = vmap.get("security")
    if sec is not None and sec["verdict"] == "n/a":
        reason = _security_floor_reason(ctx)
        if reason:
            _promote(sec, f"floor: security promoted to light — {reason}")
    arch = vmap.get("architecture")
    if arch is not None and arch["verdict"] == "n/a":
        f = _code_change_file(ctx)
        if f:
            _promote(arch, "floor: architecture promoted to light — "
                     f"code change ({f})")
    return vmap


# ------------------------------------------------------------------- budget

def apply_budget(verdict_map: dict, cap: int = DEEP_CAP,
                 target: tuple = DEEP_TARGET, ctx: Ctx | None = None) -> dict:
    """Rank deep lenses by score (ties broken by lens id) and DEMOTE — never
    drop — everything past `cap` to light, recording the demotion in
    evidence. `target` documents the desired deep band; depth is never
    manufactured to reach it. Floors run AFTER the budget when a ctx is
    given, so a floor can never be budgeted away. Returns a NEW map."""
    out = {lid: {"verdict": v["verdict"], "score": v["score"],
                 "evidence": list(v["evidence"]),
                 "negative_evidence": list(v["negative_evidence"]),
                 **({"floor": v["floor"]} if "floor" in v else {})}
           for lid, v in verdict_map.items()}
    deep = sorted((lid for lid, v in out.items() if v["verdict"] == "deep"),
                  key=lambda lid: (-out[lid]["score"], lid))
    for rank, lid in enumerate(deep, start=1):
        if rank > cap:
            entry = out[lid]
            entry["verdict"] = "light"
            entry["evidence"].append(
                f"budget: demoted deep->light (rank {rank} > cap {cap}, "
                f"score {entry['score']})")
    if ctx is not None:
        _apply_floors(out, ctx)
    return out


# -------------------------------------------------------------- entry point

def route_verdicts(workspace, files, stage=None, requirement_text=None,
                   graph=None) -> dict:
    """The one-call entry the router (t2) uses: verdicts for EVERY catalog
    lens over the changed files, budget-capped, floors applied after the
    budget. `stage` is carried on the ctx for the router's stage profiles
    (t2) — detection itself is stage-independent. Deterministic; designed to
    complete well under 1s on a repo-sized change list."""
    cat = load_catalog()
    ctx = make_ctx(workspace, files, requirement_text=requirement_text,
                   graph=graph, stage=stage)
    vmap = verdicts([l["id"] for l in cat["lenses"]], ctx, floors=False)
    return apply_budget(vmap, cap=DEEP_CAP, target=DEEP_TARGET, ctx=ctx)


# ---------------------- routed-audit hybrid MEASUREMENT (R-0006, D-0003)
#
# MEASUREMENT ONLY — audit execution is unchanged this phase either way
# (the em step's full-catalog breadth="all" mandate is untouched). This
# harness compares, on a frozen dogfood/replay corpus of this repo's own
# diffs, the CURRENT full audit shape (breadth="all": routed lenses deep +
# one full-catalog sweep brief) against the HYBRID shape (routed deep +
# light batch + ONE batched negative-evidence verification sweep over the
# n/a lenses). Token proxy: prompt BYTES of the dispatched briefs. Escaped
# finding: a corpus finding (from a real em review, lens-attributed) whose
# lens the routed path marks n/a — the exact coverage the hybrid would
# have skipped. Adoption bar (default DECLINE unless met): token reduction
# >= 30% AND zero escaped findings; the outcome is RECORDED as a decision,
# never flipped into behavior here.

HYBRID_BAR = {"min_token_reduction_pct": 30.0, "max_escaped_findings": 0}


def hybrid_verdict(token_reduction_pct: float, escaped_findings: int) -> str:
    """The D-0003 adoption bar. Default DECLINE unless the bar is met."""
    if (token_reduction_pct >= HYBRID_BAR["min_token_reduction_pct"]
            and escaped_findings <= HYBRID_BAR["max_escaped_findings"]):
        return "adopt"
    return "decline"


def _prompt_bytes(payload: dict) -> int:
    """Token proxy for one dispatch payload: UTF-8 bytes of every prompt
    that would actually be sent (deep briefs + the batched sweep brief)."""
    total = 0
    for b in payload.get("deep") or []:
        total += len((b.get("prompt") or "").encode("utf-8"))
    sw = payload.get("sweep")
    if sw:
        total += len((sw.get("prompt") or "").encode("utf-8"))
    return total


def verification_brief_prompt(decision: dict) -> str:
    """The hybrid's ONE batched negative-evidence verification sweep: a
    single prompt asking a cheap agent to verify each n/a lens's
    negative-evidence claims against the diff (never a deep review)."""
    nas = [(lid, list(d.get("negative_evidence") or []))
           for lid, d in sorted((decision or {}).items())
           if d.get("verdict") == "n/a"]
    if not nas:
        return ""
    lines = ["Batched NEGATIVE-EVIDENCE VERIFICATION sweep (routed-audit "
             "hybrid): for each n/a-routed lens below, verify its "
             "negative-evidence claims against the diff — flag any claim "
             "the diff contradicts, one line per lens. READ-ONLY."]
    for lid, claims in nas:
        lines.append(f"- {lid}: " + ("; ".join(claims) or "no claims"))
    return "\n".join(lines) + "\n"


def measure_audit_hybrid(corpus_entries, workspace=None, base: str = "HEAD",
                         stage: str = "review") -> dict:
    """Run the D-0003 comparison over corpus entries
    [{label, files, requirement_text, findings:[{lens, ...}]}] and return
    the `audit_hybrid_measured` event payload:
    {tokens_full, tokens_hybrid, token_reduction_pct, escaped_findings,
     verdict, bar, corpus_size, rows}. Deterministic for a frozen corpus.
    Imports lens at call time (lens imports THIS module at load time, so a
    module-level import would be a cycle)."""
    import lens  # noqa: runtime import — see docstring

    rows = []
    tokens_full = tokens_hybrid = escaped_total = 0
    for entry in corpus_entries:
        files = list(entry.get("files") or [])
        req = entry.get("requirement_text") or ""
        full = lens.dispatch_briefs(
            lens.route(files, breadth="all"), base=base)
        routed = lens.route(files, stage=stage, workspace=workspace,
                            requirement_text=req)
        hyb = lens.dispatch_briefs(routed, base=base)
        decision = hyb.get("routing_decision") or {}
        ftok = _prompt_bytes(full)
        htok = (_prompt_bytes(hyb)
                + len(verification_brief_prompt(decision).encode("utf-8")))
        na = {lid for lid, d in decision.items()
              if d.get("verdict") == "n/a"}
        esc = sorted({(f.get("lens") or "?") for f in
                      (entry.get("findings") or [])
                      if (f.get("lens") or "") in na})
        n_esc = sum(1 for f in (entry.get("findings") or [])
                    if (f.get("lens") or "") in na)
        tokens_full += ftok
        tokens_hybrid += htok
        escaped_total += n_esc
        rows.append({"label": entry.get("label", "?"),
                     "files": len(files), "tokens_full": ftok,
                     "tokens_hybrid": htok, "escaped_findings": n_esc,
                     "escaped_lenses": esc})
    pct = (round(100.0 * (1.0 - tokens_hybrid / tokens_full), 2)
           if tokens_full else 0.0)
    return {"event": "audit_hybrid_measured",
            "tokens_full": tokens_full,
            "tokens_hybrid": tokens_hybrid,
            "token_reduction_pct": pct,
            "escaped_findings": escaped_total,
            "verdict": hybrid_verdict(pct, escaped_total),
            "bar": dict(HYBRID_BAR),
            "corpus_size": len(rows),
            "rows": rows}
