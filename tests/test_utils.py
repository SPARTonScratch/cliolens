import pytest

from cliolens.utils import estimate_tokens, format_count, format_size, parse_size, to_posix


class TestParseSize:
    def test_plain_number_is_bytes(self):
        assert parse_size("512") == 512

    def test_units(self):
        assert parse_size("512B") == 512
        assert parse_size("100KB") == 102_400
        assert parse_size("1.5MB") == int(1.5 * 1024 * 1024)
        assert parse_size("2GB") == 2 * 1024**3

    def test_case_and_whitespace_insensitive(self):
        assert parse_size("1kb") == 1024
        assert parse_size("  4 kb ") == 4096

    @pytest.mark.parametrize("bad", ["", "abc", "12XB", "-5KB", "KB", "1..5KB"])
    def test_invalid(self, bad):
        with pytest.raises(ValueError):
            parse_size(bad)


class TestFormatSize:
    def test_bytes(self):
        assert format_size(0) == "0B"
        assert format_size(512) == "512B"

    def test_kilobytes(self):
        assert format_size(1024) == "1.0KB"
        assert format_size(102_400) == "100.0KB"

    def test_megabytes(self):
        assert format_size(1_048_576) == "1.0MB"
        assert format_size(int(450 * 1024)) == "450.0KB"


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_rounds_up(self):
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcde") == 2

    def test_linear(self):
        assert estimate_tokens("a" * 1000) == 250


class TestMisc:
    def test_format_count(self):
        assert format_count(1_234_567) == "1,234,567"

    def test_to_posix_passthrough(self):
        assert to_posix("a/b/c.txt") == "a/b/c.txt"
