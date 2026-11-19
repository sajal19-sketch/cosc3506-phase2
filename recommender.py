"""Phase 4 recommendations: a prerequisite DAG scheduled term by term with Kahn's algorithm."""

from collections import defaultdict
from typing import Dict, Iterator, List, Optional, Set

from audit_engine import codes_in, normalize_code, term_sort_key, TERM_RE

DONE_STATUSES = {"Completed", "In-Progress"}
DEFAULT_MAX_PER_TERM = 5  # 15 credits: a full-time load


# --- Terms -------------------------------------------------------------------
def next_regular_term(term: str) -> str:
    """The Fall/Winter term after `term` (spring/summer are not scheduled)."""
    match = TERM_RE.match(term)
    year, season = int(match.group(1)), match.group(2).upper()
    if season == "F":
        return f"{(year + 1) % 100:02d}W"
    return f"{year:02d}F"


def term_sequence(start: str) -> Iterator[str]:
    term = start
    while True:
        yield term
        term = next_regular_term(term)


def start_term(history: List[Dict], today_term: str) -> str:
    """First term after the student's latest history term, or `today_term` with no history."""
    terms = [h.get("term", "") for h in history if TERM_RE.match(h.get("term", "") or "")]
    if not terms:
        return today_term
    return next_regular_term(max(terms, key=term_sort_key))


def term_for_date(year: int, month: int) -> str:
    yy = year % 100
    if month <= 4:
        return f"{yy:02d}W"
    return f"{yy:02d}F"


# --- Graph -------------------------------------------------------------------
def _cross_partners(catalog: Dict[str, Dict]) -> Dict[str, Set[str]]:
    """Symmetric cross-listing map, limited to courses in the catalog."""
    partners: Dict[str, Set[str]] = defaultdict(set)
    for key, course in catalog.items():
        for other in codes_in(course.get("cross_listed", [])):
            other_key = normalize_code(other)
            if other_key != key and other_key in catalog:
                partners[key].add(other_key)
                partners[other_key].add(key)
    return partners


def _equivalence_reps(keys: Set[str], partners: Dict[str, Set[str]]) -> Dict[str, str]:
    """Collapse cross-listed groups to one representative (alphabetically first)."""
    rep: Dict[str, str] = {}
    for key in sorted(keys):
        if key in rep:
            continue
        group, stack = set(), [key]
        while stack:
            node = stack.pop()
            if node in group:
                continue
            group.add(node)
            stack.extend(p for p in partners.get(node, ()) if p in keys)
        leader = min(group)
        for node in group:
            rep[node] = leader
    return rep


def build_graph(history: List[Dict], catalog: Dict[str, Dict]):
    """Return (nodes, edges, unmet) for the courses the student still needs."""
    done = {
        normalize_code(h.get("course_code", ""))
        for h in history
        if (h.get("status") or "").strip() in DONE_STATUSES
    }
    partners = _cross_partners(catalog)
    # A completed course also covers its cross-listed equivalents.
    covered = set(done)
    for key in done:
        covered |= partners.get(key, set())

    remaining = {key for key in catalog if key not in covered}
    rep = _equivalence_reps(remaining, partners)
    nodes = set(rep.values())

    edges: Dict[str, Set[str]] = defaultdict(set)
    for node in nodes:
        for prereq in codes_in(catalog[node].get("prerequisites", [])):
            pkey = normalize_code(prereq)
            if pkey in covered:
                continue
            source = rep.get(pkey)
            if source is None or source == node:
                continue  # Not offered in the catalog: nothing we can schedule.
            edges[source].add(node)
    return nodes, edges


def _heights(nodes: Set[str], edges: Dict[str, Set[str]]) -> Dict[str, int]:
    """Longest chain of dependents below each node (critical-path priority)."""
    memo: Dict[str, int] = {}
    visiting: Set[str] = set()

    def height(node: str) -> int:
        if node in memo:
            return memo[node]
        if node in visiting:  # cycle: Kahn will leave it unscheduled anyway
            return 0
        visiting.add(node)
        value = 1 + max((height(child) for child in edges.get(node, ())), default=0)
        visiting.discard(node)
        memo[node] = value
        return value

    for node in nodes:
        height(node)
    return memo


def recommend(
    student_id: str,
    history: List[Dict],
    catalog: Dict[str, Dict],
    first_term: str,
    max_per_term: Optional[int] = DEFAULT_MAX_PER_TERM,
) -> Dict:
    nodes, edges = build_graph(history, catalog)
    indegree = {node: 0 for node in nodes}
    for children in edges.values():
        for child in children:
            indegree[child] += 1
    height = _heights(nodes, edges)

    def priority(node: str):
        return (-height[node], node)

    # Kahn's algorithm, one term per BFS layer. A course becomes available only
    # after every prerequisite was placed in an *earlier* term.
    available = sorted((n for n in nodes if indegree[n] == 0), key=priority)
    pathway: List[Dict] = []
    terms = term_sequence(first_term)
    while available:
        limit = max_per_term or len(available)
        taken, available = available[:limit], available[limit:]
        pathway.append(
            {"term": next(terms), "courses": [catalog[n]["course_code"] for n in taken]}
        )
        for node in taken:
            for child in edges.get(node, ()):
                indegree[child] -= 1
                if indegree[child] == 0:
                    available.append(child)
        available.sort(key=priority)

    result = {"student_id": student_id, "recommended_pathway": pathway}
    blocked = sorted(n for n in nodes if indegree[n] > 0)
    if blocked:  # prerequisite cycle in the catalog
        result["unschedulable"] = [catalog[n]["course_code"] for n in blocked]
    return result
