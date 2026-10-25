"""Tests for PrimarySource capture — direct mode with mocked web.

Run with: pytest tests/direct/ -v
Requires: pip install genlayer-test
"""
import json
import re
import pytest

from tests.conftest import sha256_hex

CONTRACT_PATH = "contracts/primary_source.py"


def _expect_user_error(fn, *args, match=None):
    """Assert a contract call raises a GenVM UserError.

    The contract raises `gl.vm.UserError`; inside direct mode that surfaces as
    an exception whose class name is ``UserError``. We match by class name so
    the test does not need the GenVM ``gl`` module at import time.
    """
    with pytest.raises(Exception) as exc_info:
        fn(*args)
    exc = exc_info.value
    assert type(exc).__name__ == "UserError", (
        f"expected UserError (gl.vm.UserError), got {type(exc).__name__}: {exc}"
    )
    if match is not None:
        assert match in str(exc), f"expected '{match}' in {exc!r}"


def _mock_page(vm, body, status=200):
    """Mock any URL fetch returning `body` with `status`."""
    vm.mock_web(
        r".*",
        {"status": status, "body": body},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Basic capture
# ─────────────────────────────────────────────────────────────────────────────

class TestCapture:
    def test_first_capture_creates_edition_zero(self, direct_vm, direct_deploy):
        body = "hello evidence page"
        _mock_page(direct_vm, body)
        contract = direct_deploy(CONTRACT_PATH)

        eid = contract.capture_or_recapture("https://example.org/pr/1")
        assert eid == "https://example.org/pr/1"

        assert contract.edition_count(eid) == 1
        e = contract.get_edition(eid, 0)
        assert e["digest"] == sha256_hex(body)
        assert e["status"] == "OK"
        assert e["divergent"] is False

    def test_view_publication(self, direct_vm, direct_deploy):
        """MVP includes a get_latest_edition view."""
        body = "latest check"
        _mock_page(direct_vm, body)
        contract = direct_deploy(CONTRACT_PATH)

        eid = contract.capture_or_recapture("https://example.org/x")
        latest = contract.get_latest_edition(eid)
        assert latest["digest"] == sha256_hex(body)

    def test_canonical_id_preserves_path_case(self, direct_vm, direct_deploy):
        """Scheme and host are normalized; path case is preserved (RFC 3986).

        https://example.org/Page and https://example.org/page are distinct
        resources and must not be merged into one evidence record.
        """
        _mock_page(direct_vm, "abc")
        contract = direct_deploy(CONTRACT_PATH)

        eid1 = contract.capture_or_recapture("https://EXAMPLE.org/Page")
        eid2 = contract.capture_or_recapture("https://example.org/page")
        assert eid1 == "https://example.org/Page"
        assert eid2 == "https://example.org/page"
        assert eid1 != eid2
        assert contract.edition_count(eid1) == 1
        assert contract.edition_count(eid2) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Immutability + editions
# ─────────────────────────────────────────────────────────────────────────────

class TestEditions:
    def test_recapture_same_content_no_divergence(self, direct_vm, direct_deploy):
        body = "stable content"
        _mock_page(direct_vm, body)
        contract = direct_deploy(CONTRACT_PATH)

        eid = contract.capture_or_recapture("https://example.org/doc")
        contract.capture_or_recapture("https://example.org/doc")

        assert contract.edition_count(eid) == 2
        e0 = contract.get_edition(eid, 0)
        e1 = contract.get_edition(eid, 1)
        assert e0["digest"] == e1["digest"]
        assert e0["divergent"] is False

    def test_recapture_edited_content_marks_divergent(self, direct_vm, direct_deploy):
        original = "original claim text"
        edited = "edited claim text"

        contract = direct_deploy(CONTRACT_PATH)

        _mock_page(direct_vm, original)
        eid = contract.capture_or_recapture("https://news.example/article")

        direct_vm.clear_mocks()
        _mock_page(direct_vm, edited)
        contract.capture_or_recapture(eid)

        # old edition is intact, flagged divergent
        e0 = contract.get_edition(eid, 0)
        assert e0["digest"] == sha256_hex(original)
        assert e0["divergent"] is True
        # new edition stores the new digest
        e1 = contract.get_edition(eid, 1)
        assert e1["digest"] == sha256_hex(edited)
        assert e1["divergent"] is False
        assert contract.has_diverged(eid) is True

    def test_edition_zero_never_mutated_by_recapture(self, direct_vm, direct_deploy):
        """Invariant: recapture never overwrites edition N."""
        b1, b2, b3 = "v1", "v2", "v3"
        contract = direct_deploy(CONTRACT_PATH)

        _mock_page(direct_vm, b1)
        eid = contract.capture_or_recapture("https://example.org/e")

        direct_vm.clear_mocks()
        _mock_page(direct_vm, b2)
        contract.capture_or_recapture(eid)

        direct_vm.clear_mocks()
        _mock_page(direct_vm, b3)
        contract.capture_or_recapture(eid)

        assert contract.get_edition(eid, 0)["digest"] == sha256_hex(b1)
        assert contract.get_edition(eid, 1)["digest"] == sha256_hex(b2)
        assert contract.get_edition(eid, 2)["digest"] == sha256_hex(b3)
        assert contract.edition_count(eid) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Divergent captures are data, not errors
# ─────────────────────────────────────────────────────────────────────────────

class TestDivergentAsData:
    def test_404_after_ok_is_a_new_edition_not_divergent(self, direct_vm, direct_deploy):
        """Status change is recorded; divergence flag is for OK->OK digest flips."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_page(direct_vm, "once existed")
        eid = contract.capture_or_recapture("https://example.org/dead")

        direct_vm.clear_mocks()
        _mock_page(direct_vm, "irrelevant", status=404)
        contract.capture_or_recapture(eid)

        e1 = contract.get_edition(eid, 1)
        assert e1["status"] == "NOT_FOUND"
        # status flip is not an OK->OK content divergence
        assert e1["divergent"] is False

    def test_divergence_history_persists_even_if_content_reverts(self, direct_vm, direct_deploy):
        """A past silent edit is a permanent fact — flag never un-flips."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_page(direct_vm, " original ")
        eid = contract.capture_or_recapture("https://example.org/flip")

        direct_vm.clear_mocks()
        _mock_page(direct_vm, " tampered ")
        contract.capture_or_recapture(eid)

        direct_vm.clear_mocks()
        _mock_page(direct_vm, " original ")
        contract.capture_or_recapture(eid)

        # history shows: OK -> OK(digest flip) -> OK(back to original)
        assert contract.edition_count(eid) == 3
        assert contract.has_diverged(eid) is True
        assert contract.get_edition(eid, 0)["divergent"] is True
        assert contract.get_edition(eid, 2)["digest"] == sha256_hex(" original ")


# ─────────────────────────────────────────────────────────────────────────────
# Lookup errors
# ─────────────────────────────────────────────────────────────────────────────

class TestLookups:
    def test_unknown_evidence_id_raises_on_view(self, direct_deploy):
        contract = direct_deploy(CONTRACT_PATH)
        _expect_user_error(
            contract.get_latest_edition, "https://nope.example",
            match='no such evidence',
        )

    def test_edition_out_of_range(self, direct_vm, direct_deploy):
        _mock_page(direct_vm, "only one")
        contract = direct_deploy(CONTRACT_PATH)
        eid = contract.capture_or_recapture("https://example.org/one")
        _expect_user_error(
            contract.get_edition, eid, 5,
            match='no such edition',
        )

    def test_edition_count_zero_for_unknown(self, direct_deploy):
        contract = direct_deploy(CONTRACT_PATH)
        assert contract.edition_count("https://unknown.example") == 0
        assert contract.has_diverged("https://unknown.example") is False


class TestErrorPaths:
    def test_unreachable_capture_of_existing_raises_user_error(self, direct_vm, direct_deploy):
        """A flaky network must not pollute an existing record -> UserError."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_page(direct_vm, "initial body")
        contract.capture_or_recapture("https://example.org/flaky")

        direct_vm.clear_mocks()
        # network failure -> capture UNREACHABLE on existing record -> UserError
        _expect_user_error(
            contract.capture_or_recapture, "https://example.org/flaky",
            match='UNREACHABLE',
        )
        # existing editions untouched
        assert contract.edition_count("https://example.org/flaky") == 1

    def test_unreachable_capture_of_new_record_records_unreachable_edition(self, direct_vm, direct_deploy):
        """First capture of a URL with no web mock records an UNREACHABLE edition."""
        contract = direct_deploy(CONTRACT_PATH)

        direct_vm.clear_mocks()
        # no mock -> fetch fails -> leader returns UNREACHABLE dict -> recorded
        eid = contract.capture_or_recapture("https://example.org/dead")

        assert contract.edition_count(eid) == 1
        e0 = contract.get_edition(eid, 0)
        assert e0["status"] == "UNREACHABLE"
        assert e0["digest"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# URL identity / canonicalization (RFC 3986)
# ─────────────────────────────────────────────────────────────────────────────

def _mock_url(vm, url, body, status=200):
    """Mock one specific URL returning `body` with `status`."""
    vm.mock_web(re.escape(url), {"status": status, "body": body})


class TestUrlIdentity:
    def test_path_case_remains_distinct(self, direct_vm, direct_deploy):
        """Path is case-sensitive: /Path and /path are different resources."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_url(direct_vm, "https://example.com/Path", "uppercase path")
        _mock_url(direct_vm, "https://example.com/path", "lowercase path")

        eid_upper = contract.capture_or_recapture("https://example.com/Path")
        eid_lower = contract.capture_or_recapture("https://example.com/path")

        assert eid_upper == "https://example.com/Path"
        assert eid_lower == "https://example.com/path"
        assert eid_upper != eid_lower
        assert contract.edition_count(eid_upper) == 1
        assert contract.edition_count(eid_lower) == 1

        e_upper = contract.get_edition(eid_upper, 0)
        e_lower = contract.get_edition(eid_lower, 0)
        assert e_upper["digest"] == sha256_hex("uppercase path")
        assert e_lower["digest"] == sha256_hex("lowercase path")
        assert e_upper["url"] == "https://example.com/Path"
        assert e_lower["url"] == "https://example.com/path"

    def test_query_case_remains_distinct(self, direct_vm, direct_deploy):
        """Query is case-sensitive: ?item=ABC and ?item=abc are different resources."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_url(direct_vm, "https://example.com/?item=ABC", "ABC item")
        _mock_url(direct_vm, "https://example.com/?item=abc", "abc item")

        eid_upper = contract.capture_or_recapture("https://example.com/?item=ABC")
        eid_lower = contract.capture_or_recapture("https://example.com/?item=abc")

        assert eid_upper == "https://example.com/?item=ABC"
        assert eid_lower == "https://example.com/?item=abc"
        assert eid_upper != eid_lower
        assert contract.edition_count(eid_upper) == 1
        assert contract.edition_count(eid_lower) == 1

        e_upper = contract.get_edition(eid_upper, 0)
        e_lower = contract.get_edition(eid_lower, 0)
        assert e_upper["digest"] == sha256_hex("ABC item")
        assert e_lower["digest"] == sha256_hex("abc item")
        assert e_upper["url"] == "https://example.com/?item=ABC"
        assert e_lower["url"] == "https://example.com/?item=abc"

    def test_host_and_scheme_are_normalized(self, direct_vm, direct_deploy):
        """Host and scheme are case-insensitive: different casings merge."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_url(direct_vm, "https://example.com/resource", "same body")

        eid1 = contract.capture_or_recapture("HTTPS://Example.COM/resource")
        eid2 = contract.capture_or_recapture("https://example.com/resource")

        assert eid1 == eid2 == "https://example.com/resource"
        assert contract.edition_count(eid1) == 2

    def test_fragment_is_dropped_from_identity(self, direct_vm, direct_deploy):
        """Fragment is never sent to the server; it must not affect identity."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_url(direct_vm, "https://example.com/page", "body")

        eid1 = contract.capture_or_recapture("https://example.com/page#section")
        eid2 = contract.capture_or_recapture("https://example.com/page")

        assert eid1 == eid2 == "https://example.com/page"
        assert contract.edition_count(eid1) == 2

    def test_recorded_url_matches_fetched_url(self, direct_vm, direct_deploy):
        """Every edition records the exact resource fetched (not a normalized copy)."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_url(direct_vm, "https://example.com/Path", "path body")

        eid = contract.capture_or_recapture("https://example.com/Path")
        e0 = contract.get_edition(eid, 0)

        assert e0["url"] == "https://example.com/Path"
        assert e0["digest"] == sha256_hex("path body")

    def test_recapturing_one_url_never_merges_another(self, direct_vm, direct_deploy):
        """Recapturing URL A must not modify or merge into URL B's editions."""
        contract = direct_deploy(CONTRACT_PATH)

        _mock_url(direct_vm, "https://example.com/A", "A body")
        _mock_url(direct_vm, "https://example.com/B", "B body")

        eid_a = contract.capture_or_recapture("https://example.com/A")
        eid_b = contract.capture_or_recapture("https://example.com/B")

        # recapture A again
        direct_vm.clear_mocks()
        _mock_url(direct_vm, "https://example.com/A", "A body v2")
        contract.capture_or_recapture("https://example.com/A")

        # A now has 2 editions, B still has 1 — never merged
        assert contract.edition_count(eid_a) == 2
        assert contract.edition_count(eid_b) == 1

        b0 = contract.get_edition(eid_b, 0)
        assert b0["digest"] == sha256_hex("B body")
        assert b0["url"] == "https://example.com/B"
        assert b0["divergent"] is False

        # A's second edition reflects its own new body
        a1 = contract.get_edition(eid_a, 1)
        assert a1["digest"] == sha256_hex("A body v2")
        assert a1["url"] == "https://example.com/A"
        assert a1["divergent"] is False
        # the previous edition (index 0) is flagged divergent on the OK->OK flip
        a0 = contract.get_edition(eid_a, 0)
        assert a0["digest"] == sha256_hex("A body")
        assert a0["divergent"] is True
