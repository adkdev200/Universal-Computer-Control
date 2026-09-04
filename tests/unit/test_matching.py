"""Unit tests for fuzzy text matching (design §12)."""

from universal_computer.core.matching import fuzzy_score, normalize_text, rank_matches


class TestNormalize:
    def test_casefold_and_space(self):
        assert normalize_text("  Continue  ") == "continue"

    def test_arrows_stripped(self):
        assert normalize_text("Continue →") == "continue"

    def test_punctuation_stripped(self):
        assert normalize_text("Save file?") == "save file"


class TestFuzzyScore:
    def test_exact_match(self):
        assert fuzzy_score("Continue", "Continue") == 1.0

    def test_case_insensitive(self):
        assert fuzzy_score("Continue", "continue") == 1.0

    def test_arrow_suffix(self):
        assert fuzzy_score("Continue", "Continue →") >= 0.85

    def test_small_typo(self):
        assert fuzzy_score("Continue", "Contnue") >= 0.8

    def test_unrelated(self):
        assert fuzzy_score("Continue", "File Edit View") < 0.4

    def test_empty(self):
        assert fuzzy_score("", "anything") == 0.0

    def test_token_containment(self):
        assert fuzzy_score("chrome", "Google Chrome Window") >= 0.8


class TestRankMatches:
    def test_ranking_sorted(self):
        candidates = [("Save All", 1), ("Save", 2), ("Save As...", 3), ("Delete", 4)]
        ranked = rank_matches("Save", candidates, threshold=0.6, limit=5)
        assert ranked[0][0] == 2  # exact match first
        assert all(score >= 0.6 for _payload, score in ranked)

    def test_threshold_filters(self):
        candidates = [("Completely different", 1)]
        assert rank_matches("Continue", candidates, threshold=0.9) == []

    def test_limit(self):
        candidates = [(f"Save {i}", i) for i in range(10)]
        assert len(rank_matches("Save", candidates, threshold=0.5, limit=3)) == 3
