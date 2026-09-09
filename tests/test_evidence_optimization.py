"""A1/B2/A3: Jaccard pre-filter in _sim, number-word normalization in
norm(), and no-timestamp confidence redistribution. Each test pins that
the optimizations do not let weaker matches through than before."""
import pytest


# ------------------------------------------------------------------- A1
class TestJaccardPrefilter:
    def make_spans(self, n):
        from app.knowledge.evidence import SourceSpan
        return [SourceSpan(
            f"unrelated filler sentence number {i} about cooking pasta",
            None, "transcript") for i in range(n)]

    def test_prefilter_skips_ratio_on_unrelated_spans(self, monkeypatch):
        """No shared vocabulary -> SequenceMatcher.ratio never runs."""
        import app.knowledge.evidence as ev

        calls = {"n": 0}
        real_ratio = ev.SequenceMatcher.ratio

        def counting_ratio(self):
            calls["n"] += 1
            return real_ratio(self)

        monkeypatch.setattr(ev.SequenceMatcher, "ratio", counting_ratio)

        m = ev.find_evidence(
            "best exercises for lower back pain relief", "back pain",
            self.make_spans(40))

        assert calls["n"] == 0
        assert m.similarity == 0.0
        assert m.span is None

    def test_prefilter_keeps_true_match(self):
        from app.knowledge.evidence import SourceSpan, find_evidence

        spans = [SourceSpan(
            "morning run 5k then strength training plan", 12.0, "transcript")]
        m = find_evidence("strength training plan for beginners",
                          "strength training", spans)
        assert m.similarity >= 0.55
        assert m.span is not None

    def test_hallucination_still_dropped(self):
        from app.knowledge.evidence import (HALLUCINATION_THRESHOLD,
                                            SourceSpan, find_evidence)

        spans = [SourceSpan(
            "top five budgeting apps for students in 2026", 8.0, "ocr")]
        m = find_evidence("guaranteed stock returns double your money fast",
                          "double money", spans)
        assert m.similarity < HALLUCINATION_THRESHOLD
        assert m.span is None

    def test_containment_survives_low_jaccard(self):
        """A verbatim sub-quote inside a long span scores 1.0 even though
        word-set Jaccard is far below the pre-filter threshold."""
        from app.knowledge.evidence import SourceSpan, find_evidence

        span_text = " ".join(["filler"] * 30) + " apply before september 15"
        m = find_evidence("apply before september 15", "september 15",
                          [SourceSpan(span_text, 20.0, "transcript")])
        assert m.similarity == 1.0


# ------------------------------------------------------------------- B2
class TestNumberNormalization:
    def test_number_words_become_digits(self):
        from app.knowledge.evidence import norm

        assert norm("twenty five thousand") == "25000"
        assert norm("twenty five") == "25"
        assert norm("fifteen hundred") == "1500"
        assert norm("twenty") == "20"
        assert norm("one hundred") == "100"

    def test_comma_grouped_digits_join(self):
        from app.knowledge.evidence import norm

        assert norm("25,000") == "25000"
        assert norm("₹25,000 only") == "₹25000 only"
        assert norm("1,000 subscribers") == "1000 subscribers"

    def test_words_match_digits(self):
        from app.knowledge.evidence import norm

        assert norm("twenty five thousand rupees") == norm("25,000 rupees")

    def test_digit_runs_and_unit_digits_not_merged(self):
        from app.knowledge.evidence import norm

        assert norm("8 to 10 lakh") == "8 to 10 lakh"
        assert norm("15 09 2026") == "15 09 2026"
        assert norm("nine eight seven") == "9 8 7"
        assert norm("25 thousand") == "25000"

    def test_number_word_quote_matches_digit_span(self):
        from app.knowledge.evidence import (HALLUCINATION_THRESHOLD,
                                            SourceSpan, find_evidence)

        spans = [SourceSpan("Course Fee: 25,000 only for this batch",
                            None, "ocr")]
        m = find_evidence("the full course costs twenty five thousand rupees",
                          "twenty five thousand", spans)
        assert m.similarity >= HALLUCINATION_THRESHOLD
        assert m.span is not None


# ------------------------------------------------------------------- A3
class TestNoTimestampConfidence:
    def test_weights_redistributed_without_timestamp(self):
        from app.knowledge.evidence import confidence_score

        # 0.45*0.9 + 0.2*0.5 (model_conf default) = 0.505
        assert confidence_score(0.9, 0, None,
                                has_timestamp=False) == pytest.approx(0.51)
        # multi-source agreement now worth 0.35: 0.45*0.6 + 0.35 + 0.1
        assert confidence_score(0.6, 2, None,
                                has_timestamp=False) == pytest.approx(0.72)

    def test_max_without_timestamp_reaches_099(self):
        from app.knowledge.evidence import confidence_score

        assert confidence_score(1.0, 2, 1.0,
                                has_timestamp=False) == pytest.approx(0.99)

    def test_weights_unchanged_with_timestamp(self):
        from app.knowledge.evidence import confidence_score

        assert confidence_score(0.9, 1, None,
                                has_timestamp=True) == pytest.approx(0.66)
        assert confidence_score(1.0, 2, 1.0,
                                has_timestamp=True) == pytest.approx(0.99)

    def test_no_timestamp_still_below_with_timestamp(self):
        from app.knowledge.evidence import confidence_score

        assert (confidence_score(0.9, 1, None, has_timestamp=False)
                < confidence_score(0.9, 1, None, has_timestamp=True))

    def test_explicit_model_conf_used(self):
        from app.knowledge.evidence import confidence_score

        assert confidence_score(0.8, 0, 0.0,
                                has_timestamp=False) == pytest.approx(0.36)
