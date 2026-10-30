"""Unit tests for the bracket config schema (``validate_bracket_config``).

Pure-function tests (no DB): the closed, ``extra='forbid'`` schema turns typos
and bad values into user-facing ``ValueError``s at the service boundary. Focus
here is the per-round display-metadata map (``rounds``) added for the redesigned
bracket view.
"""

import pytest

from application.services.bracket_config import validate_bracket_config


class TestRoundsConfig:
    def test_valid_rounds_map(self):
        normalized = validate_bracket_config(
            {
                "rounds": {
                    "1": {"best_of": 3, "scheduled_at": "2026-08-01T18:00:00+00:00"},
                    "-1": {"best_of": 1},
                }
            }
        )
        assert normalized["rounds"]["1"]["best_of"] == 3
        assert normalized["rounds"]["1"]["scheduled_at"] == "2026-08-01T18:00:00+00:00"
        assert normalized["rounds"]["-1"]["best_of"] == 1

    def test_empty_round_entry_allowed(self):
        normalized = validate_bracket_config({"rounds": {"2": {}}})
        assert normalized["rounds"]["2"] == {}

    def test_non_integer_round_key_rejected(self):
        with pytest.raises(ValueError, match="rounds keys"):
            validate_bracket_config({"rounds": {"final": {"best_of": 3}}})

    def test_even_best_of_rejected(self):
        with pytest.raises(ValueError, match="odd"):
            validate_bracket_config({"rounds": {"1": {"best_of": 2}}})

    def test_zero_best_of_rejected(self):
        with pytest.raises(ValueError, match="at least 1"):
            validate_bracket_config({"rounds": {"1": {"best_of": 0}}})

    def test_bad_scheduled_at_rejected(self):
        with pytest.raises(ValueError, match="ISO-8601"):
            validate_bracket_config({"rounds": {"1": {"scheduled_at": "not-a-date"}}})

    def test_bad_scheduled_end_rejected(self):
        with pytest.raises(ValueError, match="ISO-8601"):
            validate_bracket_config({"rounds": {"1": {"scheduled_end": "not-a-date"}}})

    def test_round_window_round_trips(self):
        normalized = validate_bracket_config(
            {
                "rounds": {
                    "1": {
                        "scheduled_at": "2026-08-01T18:00:00+00:00",
                        "scheduled_end": "2026-08-02T02:00:00+00:00",
                    }
                }
            }
        )
        assert normalized["rounds"]["1"]["scheduled_end"] == "2026-08-02T02:00:00+00:00"

    def test_either_half_of_the_window_may_stand_alone(self):
        normalized = validate_bracket_config(
            {"rounds": {"1": {"scheduled_end": "2026-08-02T02:00:00+00:00"}}}
        )
        assert normalized["rounds"]["1"]["scheduled_end"] == "2026-08-02T02:00:00+00:00"

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError, match="scheduled_end must be after"):
            validate_bracket_config(
                {
                    "rounds": {
                        "1": {
                            "scheduled_at": "2026-08-02T02:00:00+00:00",
                            "scheduled_end": "2026-08-01T18:00:00+00:00",
                        }
                    }
                }
            )

    def test_zero_length_window_rejected(self):
        with pytest.raises(ValueError, match="scheduled_end must be after"):
            validate_bracket_config(
                {
                    "rounds": {
                        "1": {
                            "scheduled_at": "2026-08-01T18:00:00+00:00",
                            "scheduled_end": "2026-08-01T18:00:00+00:00",
                        }
                    }
                }
            )

    def test_unknown_round_key_field_rejected(self):
        with pytest.raises(ValueError, match="Invalid bracket config"):
            validate_bracket_config({"rounds": {"1": {"bogus": 1}}})

    def test_rounds_optional(self):
        normalized = validate_bracket_config({"swiss_rounds": 3})
        assert "rounds" not in normalized


class TestTiebreakers:
    """A tiebreaker key the standings pass cannot apply must never persist.

    Config edits are DRAFT-only, so a stage started with a typo could not be
    repaired in-app — every standings read of it would raise instead.
    """

    def test_known_tiebreakers_accepted(self):
        normalized = validate_bracket_config(
            {"tiebreakers": ["buchholz", "omw", "head_to_head"]}
        )
        assert normalized["tiebreakers"] == ["buchholz", "omw", "head_to_head"]

    def test_unknown_tiebreaker_rejected(self):
        with pytest.raises(ValueError, match="unknown tiebreaker"):
            validate_bracket_config({"tiebreakers": ["h2h"]})
