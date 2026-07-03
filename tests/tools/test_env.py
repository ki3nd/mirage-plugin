from tools._env import parse_env_block, redact_secrets


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


def test_redact_secrets_masks_values():
    secrets = {"SK": "supersecret", "AK": "akid123"}
    msg = "ValidationError: aws_secret_access_key='supersecret' key=akid123 bad"
    out = redact_secrets(msg, secrets)
    assert "supersecret" not in out and "akid123" not in out
    assert out.count("***") == 2


def test_redact_secrets_ignores_empty_and_noop():
    assert redact_secrets("nothing here", {"A": "", "B": "zzz"}) == "nothing here"
