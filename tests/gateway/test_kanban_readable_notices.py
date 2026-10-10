"""Kanban notices must read like English on a phone (no spinner chrome, pids, or mid-word cuts)."""
import pytest

from gateway.kanban_watchers_notifier import _EVENT_FORMATTERS, _trim
from hermes_cli.kanban_db_dispatch import clean_worker_text


class Ev:
    def __init__(self, kind, payload):
        self.kind, self.payload = kind, payload


class Notif:
    head = "[b] @infra Kanban t_abc"
    title = "Fix the thing"
    task_id = "t_abc"
    board_tag = ""
    task = None


def render(kind, payload):
    return _EVENT_FORMATTERS[kind](Ev(kind, payload), Notif())[0]


SPINNER = " mcp__terminal… ┊ ⚡ preparing mcp__terminal… ┊ ⚡ preparing mcp__terminal…"


# --- cleaner: attack first ---
def test_cleaner_real_spinner_sample_is_empty():
    assert clean_worker_text(SPINNER) == ""


def test_cleaner_ansi_and_empty():
    assert clean_worker_text("") == ""
    assert clean_worker_text("\x1b[31m\x1b[1m\x1b[0m") == ""
    assert clean_worker_text("\x1b[32mAll good\x1b[0m \x1b]0;title\x07done") == "All good done"


def test_cleaner_keeps_real_text_around_chrome():
    out = clean_worker_text("⚡ preparing mcp__terminal… ┊ I could not push: auth failed ┊ ⚡ preparing mcp__terminal…")
    assert out == "I could not push: auth failed"


def test_cleaner_huge_single_token_is_fast_and_survives():
    tok = "A" * 200_000
    assert clean_worker_text(tok) == tok
    assert len(_trim(tok)) <= 280


def test_cleaner_dedupes_repeated_lines():
    assert clean_worker_text("same\nsame\nsame") == "same"


# --- trim ---
def test_trim_never_cuts_mid_word():
    text = "The vault note isn't configured yet because the secret is missing. " * 10
    out = _trim(text)
    assert out.endswith("…") and len(out) <= 280
    assert out[:-1].rstrip().endswith(("yet", "missing.", "the", "is", "because", "secret", "note", "vault", "configured", "isn't", "The"))


def test_trim_prefers_sentence_boundary():
    text = "First sentence is short. " + "word " * 80
    assert _trim(text, 280).startswith("First sentence is short.")
    s = "A" * 100 + ". " + "B" * 100 + ". " + "C" * 100 + "."
    assert _trim(s, 280) == "A" * 100 + ". " + "B" * 100 + "…"[:0] + "." + "…"


def test_trim_short_untouched():
    assert _trim("done  fine\n") == "done fine"


# --- the three real samples ---
def test_gave_up_spinner_sample():
    payload = {"failures": 1, "error": "pid 3379634 not alive Worker's last output:" + SPINNER}
    msg = render("gave_up", payload)
    assert msg.splitlines()[0] == "⛔ Fix the thing (t_abc) stopped after 1 failed try: its worker died mid-job. Needs unblock."
    assert len(msg.splitlines()) == 1
    for junk in ("pid", "3379634", "mcp__", "┊", "⚡", "claimer"):
        assert junk not in msg


def test_gave_up_keeps_readable_last_words_on_second_line():
    payload = {"failures": 3, "error": "pid 3403170 not alive Worker's last output: " + SPINNER + " I ran out of disk space on hz1:3302373."}
    lines = render("gave_up", payload).splitlines()
    assert len(lines) == 2
    assert "3 failed tries" in lines[0]
    assert lines[1].startswith("I ran out of disk space")
    assert "3302373" not in lines[1]


def test_crashed_hides_pid_and_claimer():
    msg = render("crashed", {"pid": 3403170, "claimer": "hz1:3302373", "error": "pid 3403170 not alive"})
    assert msg == "✖ Fix the thing (t_abc) — its worker died mid-job; retrying automatically."


def test_crashed_protocol_violation_plain_english():
    msg = render("crashed", {"protocol_violation": True, "error": "worker exited cleanly (rc=0) ... protocol violation."})
    assert "finished without reporting back" in msg and "rc=0" not in msg


def test_timed_out():
    assert render("timed_out", {"limit_seconds": 900}).startswith("⏱ Fix the thing (t_abc) ran past its 15-minute limit")


def test_summaries_cut_at_boundary_not_mid_word():
    summary = ("Vault now works for Kyle and FO Lab. " * 12) + "the vault note isn't configured"
    for kind in ("completed", "review_requested"):
        msg = render(kind, {"summary": summary})
        body = msg.split("\n", 1)[1]
        assert len(body) <= 280
        assert body.endswith(("…", ".")), body


def test_block_reason_trimmed_and_cleaned():
    msg = render("blocked", {"reason": SPINNER + " need the API key from Joey"})
    assert msg.endswith(": need the API key from Joey")


def test_block_loop_still_neutral():
    msg = render("block_loop_detected", {"reason": "waiting", "kind": "transient", "recurrences": 2})
    assert "orchestration attention" in msg and "(blocked 2x" in msg
