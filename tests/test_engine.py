from pathlib import Path

import pytest

import audit_engine as engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def catalog():
    courses = engine.parse_catalog((FIXTURES / "sample_catalog.html").read_bytes())
    return {engine.normalize_code(c["course_code"]): c for c in courses}


@pytest.fixture
def history():
    return engine.parse_transcript((FIXTURES / "sample_transcript.html").read_bytes())


@pytest.mark.parametrize(
    "raw", ["COSC 3506", "COSC-3506", "cosc3506", " cosc - 3506 "]
)
def test_normalize_code_is_format_insensitive(raw):
    assert engine.normalize_code(raw) == "COSC3506"
    assert engine.display_code(raw) == "COSC-3506"


def test_display_code_leaves_unknown_shapes():
    assert engine.display_code("weird") == "WEIRD"


def test_term_ordering():
    terms = ["26F", "24F", "26SP", "23F", "24W", "26S", "26W"]
    ordered = sorted(terms, key=engine.term_sort_key)
    assert ordered == ["23F", "24W", "24F", "26W", "26SP", "26S", "26F"]
    assert engine.term_before("26SP", "26S")
    assert not engine.term_before("26F", "26F")


def test_unknown_term_sorts_last():
    assert sorted(["bogus", "23F"], key=engine.term_sort_key) == ["23F", "bogus"]


def test_extract_codes():
    assert engine.extract_codes("COSC 3127, cosc-2007 and MATH1056") == [
        "COSC-3127",
        "COSC-2007",
        "MATH-1056",
    ]
    assert engine.extract_codes("None") == []
    assert engine.extract_codes("") == []
    assert engine.extract_codes("COSC 2007; COSC 2007") == ["COSC-2007"]


def test_parse_credits():
    assert engine.parse_credits("3") == 3
    assert engine.parse_credits("6.0 credits") == 6
    assert engine.parse_credits("") == 0


def test_parse_catalog(catalog):
    assert len(catalog) == 10
    se = catalog["COSC3506"]
    assert se["credits"] == 3
    # Phase 1 contract: raw strings exactly as written in the catalog cell
    assert se["course_code"] == "COSC 3506"
    assert se["prerequisites"] == ["COSC 2007"]
    assert se["cross_listed"] == ["ITEC 3506"]
    assert catalog["COSC4426"]["prerequisites"] == ["COSC 3127", "COSC 2007"]
    assert catalog["MATH1056"]["prerequisites"] == []
    assert catalog["MATH1056"]["cross_listed"] == []
    assert catalog["COSC4086"]["credits"] == 6


def test_split_list_and_codes_in():
    assert engine.split_list("COSC 3127, COSC 2007") == ["COSC 3127", "COSC 2007"]
    assert engine.split_list("None") == []
    assert engine.codes_in(["COSC 3127 and cosc-2007"]) == ["COSC-3127", "COSC-2007"]
    assert engine.codes_in([]) == []


def test_parse_catalog_ignores_non_catalog_tables():
    html = b"<table><tr><th>Day</th></tr><tr><td>Mon</td></tr></table><table></table>"
    assert engine.parse_catalog(html) == []


def test_parse_catalog_skips_blank_rows():
    html = (
        b"<table><tr><th>Course Code</th><th>Title</th></tr>"
        b"<tr></tr><tr><td></td><td>x</td></tr><tr><td>ABCD 1000</td></tr></table>"
    )
    courses = engine.parse_catalog(html)
    assert [c["course_code"] for c in courses] == ["ABCD 1000"]
    assert courses[0]["credits"] == 0


def test_parse_transcript_dedupes_and_filters(history):
    keys = {(h["course_code"], h["term"]) for h in history}
    assert len(history) == 8
    assert ("COSC-9999", "") not in keys
    assert ("COSC-4426", "26F") not in keys  # Planned rows are not history
    by_key = {(h["course_code"], h["term"]): h for h in history}
    # numeric grade 71 beats the P lab row for the same (course, term)
    assert by_key[("COSC-1046", "23F")]["credits_earned"] == 3
    # numeric 67 beats letter F
    assert by_key[("COSC-2006", "25W")]["credits_earned"] == 3
    assert by_key[("COSC-2006", "24F")]["status"] == "Attempted"


def test_grade_weight_order():
    w = engine._grade_weight
    assert w("71") > w("A") > w("P")
    assert w("") == 0


def test_total_earned_counts_retakes_once(history):
    assert engine.total_earned(history) == 18


def test_total_earned_later_pass_overrides_failure():
    history = [
        {"course_code": "COSC-1046", "term": "23F", "credits_earned": 0, "status": "Completed"},
        {"course_code": "COSC 1046", "term": "24W", "credits_earned": 3, "status": "Completed"},
        {"course_code": "COSC-1046", "term": "24F", "credits_earned": 3, "status": "Completed"},
        {"course_code": "COSC-2006", "term": "24F", "credits_earned": 3, "status": "Attempted"},
        {"course_code": "COSC-2007", "term": "25W", "credits_earned": 3, "status": "In-Progress"},
    ]
    assert engine.total_earned(history) == 3


def test_cross_listed_is_symmetric():
    catalog = {
        "COSC3506": {"cross_listed": ["ITEC-3506"]},
        "ITEC3506": {"cross_listed": []},
    }
    assert engine.cross_listed_with("ITEC 3506", catalog) == ["COSC3506"]
    assert engine.cross_listed_with("cosc3506", catalog) == ["ITEC3506"]
    assert engine.cross_listed_with("NOPE1000", catalog) == []


def test_full_audit(catalog, history):
    plan = [
        {"course_code": "COSC-4426", "term": "26F"},
        {"course_code": "ITEC 3506", "term": "26F"},
        {"course_code": "cosc3127", "term": "26W"},
        {"course_code": "COSC-4086", "term": "26W"},
        {"course_code": "UNKN-1000", "term": "26W"},
    ]
    report = engine.build_audit("770001", history, plan, catalog)
    assert report["student_id"] == "770001"
    assert report["status"] == "warning"
    assert [t["term"] for t in report["timeline_validation"]] == ["26W", "26F"]
    assert report["timeline_validation"][0]["errors"] == [
        {
            "course_code": "COSC-4086",
            "type": "MISSING_PREREQUISITE",
            "message": "Missing prerequisite: COSC-3506",
        }
    ]
    assert report["timeline_validation"][1]["errors"] == [
        {
            "course_code": "COSC-4426",
            "type": "MISSING_PREREQUISITE",
            "message": "Missing prerequisite: COSC-3127",
        }
    ]
    assert report["cross_list_violations"] == [
        {
            "course_code": "ITEC-3506",
            "type": "CROSS_LIST_CONFLICT",
            "message": "Cross-listed with completed course COSC-3506",
        }
    ]
    assert report["credit_summary"] == {
        "total_earned": 18,
        "total_planned": 15,
        "total_remaining_for_graduation": 87,
    }


def test_strict_mode_fails(catalog, history):
    plan = [{"course_code": "COSC-4426", "term": "26F"}]
    assert engine.build_audit("1", history, plan, catalog, strict=True)["status"] == "failed"


def test_clean_plan_is_ok(catalog, history):
    plan = [{"course_code": "COSC-3127", "term": "26F"}]
    for strict in (False, True):
        report = engine.build_audit("1", history, plan, catalog, strict=strict)
        assert report["status"] == "ok"
        assert report["timeline_validation"] == []
        assert report["cross_list_violations"] == []


def test_remaining_never_negative(catalog):
    history = [
        {"course_code": f"ELEC-{1000 + i}", "term": "23F", "credits_earned": 3, "status": "Completed"}
        for i in range(45)
    ]
    report = engine.build_audit("1", history, [], catalog)
    assert report["credit_summary"]["total_earned"] == 135
    assert report["credit_summary"]["total_remaining_for_graduation"] == 0


def test_duplicate_planned_course_counted_once(catalog):
    plan = [
        {"course_code": "MATH-1056", "term": "26F"},
        {"course_code": "MATH 1056", "term": "26F"},
    ]
    assert engine.total_planned(plan, catalog) == 3


def test_prereq_completed_same_term_is_not_enough(catalog):
    history = [
        {"course_code": "COSC-1046", "term": "26F", "credits_earned": 3, "status": "Completed"}
    ]
    plan = [{"course_code": "COSC-1047", "term": "26F"}]
    report = engine.build_audit("1", history, plan, catalog)
    assert report["timeline_validation"][0]["term"] == "26F"


def test_lookup(catalog):
    assert engine.lookup(catalog, "cosc-3506")["title"] == "Software Engineering"
    assert engine.lookup(catalog, "XXXX 0000") is None
