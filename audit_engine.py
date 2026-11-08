"""Pure academic-rules engine for the Phase 3 degree audit.

Everything here is framework-free so it can be unit tested in isolation.
"""

import re
from typing import Dict, Iterable, List, Optional, Tuple

from bs4 import BeautifulSoup

GRADUATION_TARGET = 120
SEASON_ORDER = {"W": 0, "SP": 1, "S": 2, "F": 3}
TERM_RE = re.compile(r"^\s*(\d{2})\s*(SP|W|S|F)\s*$", re.IGNORECASE)
COURSE_CODE_RE = re.compile(r"[A-Za-z]{2,5}[\s\-]*\d{3,4}[A-Za-z]?")
SPLIT_CODE_RE = re.compile(r"^([A-Z]+)(\d+[A-Z]?)$")
EMPTY_VALUES = {"", "none", "n/a", "na", "-", "—"}
PAST_STATUSES = {"Completed", "In-Progress", "Attempted"}


# ---------------------------------------------------------------------------
# Course codes and terms
# ---------------------------------------------------------------------------
def normalize_code(code: str) -> str:
    """Format-insensitive key: upper-case, spaces and hyphens removed."""
    return re.sub(r"[\s\-]+", "", code or "").upper()


def display_code(code: str) -> str:
    """Canonical display form, e.g. 'cosc 3506' -> 'COSC-3506'."""
    key = normalize_code(code)
    match = SPLIT_CODE_RE.match(key)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return key


def term_sort_key(term: str) -> Tuple[int, int, str]:
    """Chronological key for terms like 23F, 24W, 26SP (W < SP < S < F)."""
    match = TERM_RE.match(term or "")
    if not match:
        # Unknown formats sort last but stay deterministic.
        return (10_000, 10_000, (term or "").upper())
    year = int(match.group(1))
    season = SEASON_ORDER[match.group(2).upper()]
    return (year, season, "")


def term_before(earlier: str, later: str) -> bool:
    return term_sort_key(earlier) < term_sort_key(later)


def extract_codes(text: str) -> List[str]:
    """Pull every course code out of a free-text cell."""
    if not text or text.strip().lower() in EMPTY_VALUES:
        return []
    seen: List[str] = []
    for raw in COURSE_CODE_RE.findall(text):
        code = display_code(raw)
        if code not in seen:
            seen.append(code)
    return seen


def parse_credits(value: str) -> int:
    match = re.search(r"\d+(?:\.\d+)?", value or "")
    return int(float(match.group(0))) if match else 0


# ---------------------------------------------------------------------------
# Catalog (Phase 1)
# ---------------------------------------------------------------------------
def _find_column(headers: List[str], needle: str, default: int) -> int:
    for idx, header in enumerate(headers):
        if needle in header:
            return idx
    return default


def parse_catalog(html: bytes) -> List[Dict]:
    """Parse every catalog-shaped table into course records."""
    soup = BeautifulSoup(html, "html.parser")
    courses: List[Dict] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [
            cell.get_text(" ", strip=True).lower()
            for cell in rows[0].find_all(["th", "td"])
        ]
        if not any("code" in h or "course" in h for h in headers):
            continue
        col = {
            "code": _find_column(headers, "code", 0),
            "title": _find_column(headers, "title", 1),
            "credits": _find_column(headers, "credit", 2),
            "prereqs": _find_column(headers, "prereq", 3),
            "cross": _find_column(headers, "cross", 4),
        }

        def cell(cells, name):
            idx = col[name]
            if idx < len(cells):
                return cells[idx].get_text(" ", strip=True)
            return ""

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            raw_code = cell(cells, "code")
            if not cells or not raw_code:
                continue
            courses.append(
                {
                    "course_code": raw_code,
                    "title": cell(cells, "title"),
                    "credits": parse_credits(cell(cells, "credits")),
                    "prerequisites": extract_codes(cell(cells, "prereqs")),
                    "cross_listed": extract_codes(cell(cells, "cross")),
                }
            )
    return courses


# ---------------------------------------------------------------------------
# Transcript (Phase 2)
# ---------------------------------------------------------------------------
def _grade_weight(grade: str) -> int:
    """Numeric grade > letter grade > P/blank."""
    grade = (grade or "").strip()
    if not grade or grade.upper() == "P":
        return 0
    try:
        return 1000 + int(float(grade))
    except ValueError:
        return 500 - ord(grade[0].upper())


def parse_transcript(html: bytes) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    best: Dict[Tuple[str, str], Dict] = {}
    order: List[Tuple[str, str]] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        status = cells[0].get_text(strip=True)
        course = cells[1].get_text(strip=True)
        grade = cells[3].get_text(strip=True)
        term = cells[4].get_text(strip=True)
        credits_text = cells[5].get_text(strip=True)
        if status not in PAST_STATUSES or not term or not course:
            continue
        credits = int(credits_text) if credits_text.isdigit() else 0
        record = {
            "course_code": course,
            "term": term,
            "credits_earned": credits,
            "status": status,
            "_weight": _grade_weight(grade),
        }
        key = (course, term)
        current = best.get(key)
        if current is None:
            best[key] = record
            order.append(key)
        elif (record["_weight"], record["credits_earned"]) > (
            current["_weight"],
            current["credits_earned"],
        ):
            best[key] = record
    result = []
    for key in order:
        record = dict(best[key])
        record.pop("_weight")
        result.append(record)
    return result


# ---------------------------------------------------------------------------
# Audit (Phase 3)
# ---------------------------------------------------------------------------
def _completed(history: Iterable[Dict]) -> List[Dict]:
    return [h for h in history if (h.get("status") or "").strip() == "Completed"]


def total_earned(history: Iterable[Dict]) -> int:
    """Each completed course counts once; a later pass overrides a failure."""
    per_course: Dict[str, int] = {}
    for entry in _completed(history):
        key = normalize_code(entry.get("course_code", ""))
        credits = int(entry.get("credits_earned") or 0)
        per_course[key] = max(per_course.get(key, 0), credits)
    return sum(per_course.values())


def total_planned(plan: Iterable[Dict], catalog: Dict[str, Dict]) -> int:
    seen = set()
    total = 0
    for item in plan:
        key = normalize_code(item.get("course_code", ""))
        if key in seen:
            continue
        seen.add(key)
        course = catalog.get(key)
        if course:
            total += int(course.get("credits") or 0)
    return total


def cross_listed_with(code: str, catalog: Dict[str, Dict]) -> List[str]:
    """Symmetric cross-list lookup (A lists B, or B lists A)."""
    key = normalize_code(code)
    partners: List[str] = []
    course = catalog.get(key)
    if course:
        partners.extend(normalize_code(c) for c in course.get("cross_listed", []))
    for other_key, other in catalog.items():
        others = {normalize_code(c) for c in other.get("cross_listed", [])}
        if key in others and other_key not in partners:
            partners.append(other_key)
    return [p for p in partners if p != key]


def build_audit(
    student_id: str,
    history: List[Dict],
    plan: List[Dict],
    catalog: Dict[str, Dict],
    strict: bool = False,
) -> Dict:
    completed = _completed(history)
    completed_terms: Dict[str, List[str]] = {}
    for entry in completed:
        key = normalize_code(entry.get("course_code", ""))
        completed_terms.setdefault(key, []).append(entry.get("term", ""))

    # 1. Prerequisites, checked against the chronological timeline.
    errors_by_term: Dict[str, List[Dict]] = {}
    for item in plan:
        code = item.get("course_code", "")
        planned_term = item.get("term", "")
        course = catalog.get(normalize_code(code))
        if not course:
            continue
        for prereq in course.get("prerequisites", []):
            terms = completed_terms.get(normalize_code(prereq), [])
            if any(term_before(t, planned_term) for t in terms):
                continue
            errors_by_term.setdefault(planned_term, []).append(
                {
                    "course_code": display_code(code),
                    "type": "MISSING_PREREQUISITE",
                    "message": f"Missing prerequisite: {display_code(prereq)}",
                }
            )

    timeline_validation = [
        {"term": term, "errors": errors_by_term[term]}
        for term in sorted(errors_by_term, key=term_sort_key)
    ]

    # 2. Cross-listing conflicts (don't earn credit twice).
    cross_list_violations: List[Dict] = []
    flagged = set()
    for item in plan:
        code = item.get("course_code", "")
        for partner in cross_listed_with(code, catalog):
            if partner in completed_terms and (normalize_code(code), partner) not in flagged:
                flagged.add((normalize_code(code), partner))
                cross_list_violations.append(
                    {
                        "course_code": display_code(code),
                        "type": "CROSS_LIST_CONFLICT",
                        "message": (
                            "Cross-listed with completed course "
                            f"{display_code(partner)}"
                        ),
                    }
                )

    # 3. Credit tallies.
    earned = total_earned(history)
    planned = total_planned(plan, catalog)
    remaining = max(0, GRADUATION_TARGET - earned - planned)

    has_issues = bool(timeline_validation or cross_list_violations)
    if not has_issues:
        status = "ok"
    else:
        status = "failed" if strict else "warning"

    return {
        "student_id": student_id,
        "status": status,
        "timeline_validation": timeline_validation,
        "cross_list_violations": cross_list_violations,
        "credit_summary": {
            "total_earned": earned,
            "total_planned": planned,
            "total_remaining_for_graduation": remaining,
        },
    }


def lookup(catalog: Dict[str, Dict], code: str) -> Optional[Dict]:
    return catalog.get(normalize_code(code))
