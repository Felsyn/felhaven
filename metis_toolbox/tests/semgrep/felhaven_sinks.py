"""
felhaven_sinks.py — the cases semgrep checks felhaven_sinks.yml against.

semgrep's own test format: a "ruleid" comment naming the rule says the next
line must be reported, an "ok" comment says it must not (semgrep reads any
such comment followed by a colon as a case, so this docstring avoids the
exact form). Run it with
    semgrep scan --metrics=off --disable-version-check --test tests\\semgrep
(the full command is in felhaven_sinks.yml). This file is never imported or
run; the launch and network guards skip tests/, so its deliberate bad calls
don't trip them, and unittest discovery doesn't collect it.
"""

import asyncio
import ctypes
import json
import os
import subprocess
import subprocess as sp
import webbrowser
from subprocess import run

import requests

from tools import hermes


# ── Each source reaches a sink ───────────────────────────────────────────────

def from_hermes(address):
    text = hermes.get(address, cap=4096, timeout=5).text
    # ruleid: felhaven-untrusted-into-process-or-opener
    subprocess.run(["yt-dlp", text])


def from_a_bounded_read(sock):
    data = hermes.read_bounded(sock, 4096)
    # ruleid: felhaven-untrusted-into-process-or-opener
    os.startfile(data)


def from_requests(address):
    link = requests.get(address, timeout=5).text
    # ruleid: felhaven-untrusted-into-process-or-opener
    webbrowser.open(link)


def from_a_config_file(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    # ruleid: felhaven-untrusted-into-process-or-opener
    subprocess.Popen(["notepad.exe", cfg["file"]])


def from_the_environment():
    program = os.environ.get("EDITOR", "")
    # ruleid: felhaven-untrusted-into-process-or-opener
    os.system(program)


def from_another_programs_output():
    # ok: felhaven-untrusted-into-process-or-opener
    proc = subprocess.run(["x"], capture_output=True)
    out = proc.stdout
    # ruleid: felhaven-untrusted-into-process-or-opener
    ctypes.windll.kernel32.CreateProcessW(None, out, None, None, False, 0, None, None, None, None)


# ── LLM-facing handlers: their arguments are sources ──────────────────────────

def handle(args):
    # ruleid: felhaven-untrusted-into-process-or-opener
    ctypes.windll.shell32.ShellExecuteW(0, "open", args["path"], None, None, 1)


def describe(url):
    # ruleid: felhaven-untrusted-into-process-or-opener
    sp.run(["yt-dlp", url])


def search(query):
    # ruleid: felhaven-untrusted-into-process-or-opener
    run(["yt-dlp", query])


async def play(url):
    # ruleid: felhaven-untrusted-into-process-or-opener
    await asyncio.create_subprocess_exec("mpv", url)


# ── The shell's file operations and Tcl ───────────────────────────────────────

def recycle_from_the_network(shell32, address):
    path = hermes.get(address, cap=4096, timeout=5).text
    # ruleid: felhaven-untrusted-into-process-or-opener
    shell32.SHFileOperationW(path)


def a_win32_launcher_by_its_bare_name(shell32, address):
    ShellExecuteW = shell32.ShellExecuteW
    target = hermes.get(address, cap=4096, timeout=5).text
    # ruleid: felhaven-untrusted-into-process-or-opener
    ShellExecuteW(0, "open", target, None, None, 1)


def tcl_from_the_network(root, address):
    script = hermes.get(address, cap=4096, timeout=5).text
    # ruleid: felhaven-untrusted-into-process-or-opener
    root.tk.eval(script)
    # ruleid: felhaven-untrusted-into-process-or-opener
    root.tk.call("exec", script)


# ── Not reported ──────────────────────────────────────────────────────────────

def constants_only():
    # ok: felhaven-untrusted-into-process-or-opener
    subprocess.run(["netsh", "wlan", "show", "interfaces"])


def not_llm_facing(name):
    # ok: felhaven-untrusted-into-process-or-opener
    subprocess.run(["notepad.exe", name])


def read_but_never_launched(address):
    text = hermes.get(address, cap=4096, timeout=5).text
    # ok: felhaven-untrusted-into-process-or-opener
    return len(text)


def a_tcl_call_that_is_not_exec(root, address):
    title = hermes.get(address, cap=4096, timeout=5).text
    # ok: felhaven-untrusted-into-process-or-opener
    root.tk.call("wm", "title", ".", title)
