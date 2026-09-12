"""Deliberately contains a real bandit-catchable SAST finding (B602:
subprocess call with shell=True) so Layer 4's security-scan test proves a
real SAST tool catches a real vulnerable pattern."""
import subprocess


def run_user_command(cmd: str) -> None:
    subprocess.call(cmd, shell=True)
