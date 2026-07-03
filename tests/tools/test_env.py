from tools._env import parse_env_block


def test_basic():
    assert parse_env_block("A=1\nB=two") == {"A": "1", "B": "two"}


def test_comments_blank():
    assert parse_env_block("# c\n\nA=1\n") == {"A": "1"}


def test_equals_and_quotes():
    assert parse_env_block('U=redis://h:6379/0\nT="x=y"') == {"U": "redis://h:6379/0", "T": "x=y"}


def test_strip():
    assert parse_env_block("  A = 1 ") == {"A": "1"}


def test_empty():
    assert parse_env_block("") == {}
