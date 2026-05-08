"""Notifier should chunk safely and never send in dry-run."""

from __future__ import annotations

from src.notifier import _chunk, send_whatsapp


def test_chunk_short_message_single():
    out = _chunk("hello world")
    assert out == ["hello world"]


def test_chunk_long_message_splits_on_paragraphs():
    para = "x" * 500
    text = "\n\n".join([para] * 5)
    chunks = _chunk(text, limit=1100)
    assert len(chunks) >= 2
    for c in chunks:
        assert len(c) <= 1100


def test_dry_run_does_not_call_twilio(capsys):
    send_whatsapp("hello", dry_run=True)
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    assert "hello" in captured.out
