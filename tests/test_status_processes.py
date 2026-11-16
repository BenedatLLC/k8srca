"""`k8srca status` says whose poller and orchestrator it found.

On a shared machine another account's processes match by name. They run that
account's .env and state, so counting them silently as ours is misleading.
"""

import getpass
import os

from k8srca.bringup import Proc, describe, running

ME = os.getuid()
VENV = "/home/x/k8srca/.venv/bin/python3 /home/x/k8srca/.venv/bin/k8srca"


def ps(*rows: tuple[int, int, str]) -> str:
    return "\n".join(f"{uid:>5} {pid:>7} {args}" for uid, pid, args in rows)


def test_finds_the_subcommand_with_its_pid_and_user():
    out = ps((ME, 101, f"{VENV} poller"), (ME, 102, f"{VENV} slack run"), (ME, 103, "vim poller"))
    assert running("poller", out) == [Proc(101, getpass.getuser())]
    assert running("slack run", out) == [Proc(102, getpass.getuser())]


def test_an_unknown_uid_is_reported_by_number():
    out = ps((987654, 7, f"{VENV} poller"))
    assert running("poller", out) == [Proc(7, "987654")]


def test_own_process_is_not_a_warning():
    detail, foreign = describe([Proc(5, getpass.getuser())], "poller")
    assert not foreign
    assert "pid 5" in detail


def test_another_users_process_is_a_warning_naming_them():
    detail, foreign = describe([Proc(5, "someone-else")], "poller")
    assert foreign
    assert "someone-else" in detail and getpass.getuser() in detail
