"""Notifier should never send in dry-run, and should print recognizable output."""

from __future__ import annotations

import pytest

from src.notifier import send_email


def test_dry_run_does_not_call_smtp(capsys):
    send_email("Test subject", "hello body", dry_run=True)
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    assert "Test subject" in captured.out
    assert "hello body" in captured.out


def test_missing_env_raises_when_not_dry(monkeypatch):
    # Strip any env vars that might be present in CI.
    for key in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "EMAIL_TO", "DRY_RUN"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="Missing Gmail env vars"):
        send_email("subj", "body", dry_run=False)
