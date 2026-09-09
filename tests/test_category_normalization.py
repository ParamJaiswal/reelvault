"""D2 — edge cases for _normalize_categories (app/ai/router.py).

Complements (does not repeat) tests/test_audit_p0.py A5 (verbatim-valid
survival of list-form entries, "personal advice.", alias "advice", unknown
junk) and TestRouterGuardrails.test_category_normalization in
tests/test_deadlines.py (alias trio, "Job" str input, [42], "AI/ML,
Career" blob). Covers: verbatim string input, alias dedup, separator blob
variants, blank/dot-only pieces, mixed non-string entries, non-list
garbage, empty list, and >4-category truncation (first-seen order, after
dedup).
"""
from app.ai.router import _normalize_categories


def test_verbatim_string_input_personal_advice_survives():
    assert _normalize_categories("Personal Advice") == ["Personal Advice"]
    # case-insensitive fallback against the valid list, any casing
    assert _normalize_categories(["PERSONAL ADVICE"]) == ["Personal Advice"]


def test_data_science_alias_dedup():
    assert _normalize_categories(["Data Science"]) == ["Data Science"]
    # alias variants collapse into one canonical entry
    assert _normalize_categories(["Data Science", "ds", "ds."]) == \
        ["Data Science"]
    # "machine learning" deliberately aliases to AI/ML, not Data Science
    assert _normalize_categories(["Data Science", "machine learning"]) == \
        ["Data Science", "AI/ML"]
    assert _normalize_categories(["data analytics", "Data Science"]) == \
        ["Data Analytics", "Data Science"]


def test_separator_blob_variants():
    # semicolons split too (not covered elsewhere)
    assert _normalize_categories(["Career; Data Science"]) == \
        ["Career", "Data Science"]
    # trailing comma and runs of whitespace are harmless
    assert _normalize_categories(["Job,   AI/ML,"]) == ["Job", "AI/ML"]
    # a plain string (not a list) splits the same way
    assert _normalize_categories("AI/ML, Career") == ["AI/ML", "Career"]
    # trailing periods are stripped on the alias path
    assert _normalize_categories(["Job.", "Career."]) == ["Job", "Career"]


def test_blank_and_punctuation_only_pieces_dropped():
    assert _normalize_categories(["  ", ".", "", ".."]) == []


def test_mixed_non_string_entries_filtered():
    assert _normalize_categories([None, 3.14, "Job", "  ", "."]) == ["Job"]


def test_garbage_top_level_inputs_return_empty():
    assert _normalize_categories(42) == []
    assert _normalize_categories(None) == []
    assert _normalize_categories({"a": 1}) == []
    assert _normalize_categories(True) == []


def test_empty_list_returns_empty():
    assert _normalize_categories([]) == []


def test_dedup_across_alias_and_verbatim():
    assert _normalize_categories(["Tutorial", "how-to", "tutorials",
                                  "tutorial."]) == ["Tutorial"]


def test_truncates_to_four_categories():
    six = ["Job", "Internship", "Career", "Tutorial", "Educational", "AI/ML"]
    assert _normalize_categories(six) == \
        ["Job", "Internship", "Career", "Tutorial"]


def test_truncation_after_dedup_keeps_first_seen():
    # "Machine Learning" dedups into the already-seen AI/ML slot, so the
    # 4-slot cap must keep the first four DISTINCT categories
    assert _normalize_categories(
        ["ai/ml", "Machine Learning", "Job", "Tutorial", "Career",
         "Finance"]) == ["AI/ML", "Job", "Tutorial", "Career"]
