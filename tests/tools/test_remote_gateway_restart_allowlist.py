"""ssh to an allowlisted OTHER box may restart that box's gateway; nothing else is relaxed."""

import json

import pytest

from cron.lifecycle_guard import scan_gateway_lifecycle
from tools.terminal_tool_guards import gateway_lifecycle_block

HOSTS = ["folab", "jarvis", "fo-lab-ts", "jarvis-ts"]
RESTART = "systemctl --user restart hermes-gateway-folab"


def blocked(command, hosts=HOSTS):
    return scan_gateway_lifecycle(command, remote_ssh_hosts=hosts)[0]


@pytest.mark.parametrize("command", [
    f"ssh folab '{RESTART}'",
    f'ssh folab "{RESTART}"',
    "ssh jarvis 'systemctl --user restart hermes-gateway'",
    "ssh fo-lab-ts 'systemctl --user stop hermes-gateway-folab'",
    f"ssh -o BatchMode=yes -o ConnectTimeout=5 folab '{RESTART}'",
    f"ssh -i ~/.ssh/key -p 2222 joey@jarvis-ts '{RESTART}'",
    f"ssh -- folab '{RESTART}'",
    f"timeout 60 ssh folab '{RESTART}'",
    f"ssh folab '{RESTART}' | tail -3",
    f"ssh folab '{RESTART}' && ssh jarvis '{RESTART}'",
    "ssh folab 'hermes gateway restart'",
])
def test_allowed_remote_host_passes(command):
    assert not blocked(command), command


@pytest.mark.parametrize("command", [
    RESTART,
    "systemctl --user restart hermes-gateway",
    "hermes gateway restart",
    "pkill -f 'hermes gateway'",
])
def test_local_restart_stays_blocked_even_with_allowlist(command):
    assert blocked(command)


@pytest.mark.parametrize("host", ["localhost", "hz1", "127.0.0.1", "typo-folab", "folab.evil.example", "::1", "0.0.0.0"])
def test_ssh_to_unlisted_or_local_host_blocked(host):
    assert blocked(f"ssh {host} '{RESTART}'")
    assert blocked(f"ssh joey@{host} '{RESTART}'")


def test_local_hostname_blocked_even_if_listed(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "gethostname", lambda: "hz1")
    monkeypatch.setattr(socket, "getfqdn", lambda: "hz1.example.net")
    assert blocked(f"ssh hz1 '{RESTART}'", hosts=["hz1", "localhost", "127.0.0.1"])
    assert blocked(f"ssh localhost '{RESTART}'", hosts=["localhost"])
    assert blocked(f"ssh 127.0.0.1 '{RESTART}'", hosts=["127.0.0.1"])


@pytest.mark.parametrize("command", [
    f"ssh folab true; {RESTART}",
    "ssh folab uptime; systemctl --user restart hermes-gateway",
    f"ssh folab uptime && {RESTART}",
    f"ssh folab uptime | {RESTART}",
    f"ssh folab uptime\n{RESTART}",
    f"ssh folab '{RESTART}'; hermes gateway restart",
    f"ssh folab '{RESTART}'; ssh hz1 '{RESTART}'",
    f"echo hi || {RESTART}",
    f"ssh folab \"{RESTART}\" $({RESTART})",
    f"ssh folab \"$({RESTART})\"",
    f"ssh folab `{RESTART}`",
    f"ssh folab '{RESTART}' ; (hermes gateway stop)",
    f"bash -c \"ssh folab 'true'; {RESTART}\"",
    f"bash -c \"ssh folab '{RESTART}'\"",  # nested shells are not unwrapped: fail closed
])
def test_chained_or_smuggled_local_restart_blocked(command):
    assert blocked(command), command


@pytest.mark.parametrize("command", [
    f"ssh -o ProxyCommand='{RESTART}' folab uptime",
    f"ssh -o HostName=127.0.0.1 folab '{RESTART}'",
    f"ssh -oHostName=localhost folab '{RESTART}'",
    f"ssh -F /tmp/evil.conf folab '{RESTART}'",
    f"ssh -o LocalCommand='{RESTART}' folab uptime",
    f"ssh folab < /dev/null && {RESTART}",
    "ssh folab",  # no payload: nothing allowlisted to mask, and nothing to block either
])
def test_ssh_option_tricks(command):
    # every case that carries a local lifecycle command or retargets the host must be blocked
    if "ssh folab" == command:
        assert not blocked(command)
    else:
        assert blocked(command), command


def test_empty_or_malformed_allowlist_is_strict():
    for hosts in ([], None, "", 5, [None, 3], {}):
        assert blocked(f"ssh folab '{RESTART}'", hosts=hosts), hosts


def test_string_allowlist_is_accepted_as_single_host():
    assert not blocked(f"ssh folab '{RESTART}'", hosts="folab")


def test_default_no_allowlist_blocks_remote_restart():
    assert scan_gateway_lifecycle(f"ssh folab '{RESTART}'")[0]


# --- end to end through the terminal guard, config-driven --------------------------------------

def _run(monkeypatch, tmp_path, command, hosts):
    from tools import process_registry

    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)
    monkeypatch.setattr(
        "hermes_cli.config.read_raw_config",
        lambda: {"terminal": {"remote_gateway_restart_hosts": hosts}} if hosts is not None else {"terminal": None},
    )
    return gateway_lifecycle_block(
        command=command, env=None, env_type="local", cwd=str(tmp_path), workdir=None, session_key="remote-ssh-test",
    )


def test_terminal_guard_reads_config_allowlist(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, f"ssh folab '{RESTART}'", HOSTS) is None
    assert _run(monkeypatch, tmp_path, f"ssh folab '{RESTART}'", None) is not None
    assert _run(monkeypatch, tmp_path, f"ssh folab '{RESTART}'", []) is not None
    for bad in (f"ssh localhost '{RESTART}'", f"ssh hz1 '{RESTART}'", RESTART,
                f"ssh folab true; {RESTART}"):
        result = json.loads(_run(monkeypatch, tmp_path, bad, HOSTS))
        assert result["exit_code"] == 1, bad


def test_terminal_guard_survives_broken_config(monkeypatch, tmp_path):
    from tools import process_registry

    monkeypatch.setattr(process_registry, "_is_supervised_gateway_process", lambda: True)

    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("hermes_cli.config.read_raw_config", boom)
    assert gateway_lifecycle_block(
        command=f"ssh folab '{RESTART}'", env=None, env_type="local", cwd=str(tmp_path),
        workdir=None, session_key="remote-ssh-test",
    ) is not None
