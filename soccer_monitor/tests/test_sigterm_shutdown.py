"""
Regression test: the daemon ignored SIGTERM and was SIGKILLed by systemd on every
restart, 90 seconds later.

main() awaits an asyncio.gather() of loops that all run forever, and nothing ever
cancelled them. aiogram made it worse by installing its own SIGTERM handler
(handle_signals defaults to True), which replaced Python's default "terminate on
SIGTERM" with one that only stopped Telegram polling -- so the signal was swallowed
and the process hung until systemd's 90s stop timeout expired. The
`except asyncio.CancelledError: "Shutting down gracefully..."` arm in main() was
therefore unreachable dead code.

main() now installs its own SIGTERM/SIGINT handler that cancels the gather, and
bot_ui passes handle_signals=False so aiogram leaves the signal alone.

Runs the real daemon in SIMULATION_MODE (fully offline: mock schedule, mock markets,
mock scores, SimulatedWebsocket), sends it a SIGTERM and requires a prompt, clean,
*graceful* exit.

Scope note: this runs without a Telegram token, so it does not reproduce the aiogram
half of the bug -- that needs a live bot token and network. It covers the part that
made the process hang, namely that a SIGTERM now cancels the loops and reaches the
graceful-shutdown path. Without the fix the process dies by signal instead, and the
"Shutting down gracefully" assertion below fails.
Run: python tests/test_sigterm_shutdown.py
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = os.path.join(REPO, "soccer_monitor.py")
# ConfigManager resolves this from the script directory, not cwd, so the daemon reads
# the real file however the test is launched. It holds live league toggles and
# thresholds, so snapshot it rather than trust that a simulation run never saves.
CONFIG = os.path.join(REPO, "config_state.json")

# Logged from OrderBookMonitor.run() once the loops are up, so the signal handler
# main() installs before awaiting the gather is certainly in place by then.
READY = "Connecting to WS"
GRACEFUL = "Shutting down gracefully"

STARTUP_TIMEOUT = 60   # generous: first run may import a cold nix store
SHUTDOWN_TIMEOUT = 15  # systemd allows 90s; anything near that is the bug


def read(path):
    with open(path, "r", errors="replace") as f:
        return f.read()


def main():
    config_before = None
    if os.path.exists(CONFIG):
        with open(CONFIG, "rb") as f:
            config_before = f.read()
    try:
        run()
    finally:
        if config_before is None:
            if os.path.exists(CONFIG):
                os.unlink(CONFIG)          # the daemon created it; leave no trace
        else:
            with open(CONFIG, "wb") as f:  # put the real config back byte for byte
                f.write(config_before)


def run():
    with tempfile.TemporaryDirectory() as workdir:
        log_path = os.path.join(workdir, "daemon.log")
        env = dict(os.environ)
        env.update({
            "SIMULATION_MODE": "1",
            # Explicitly blank so a stray .env cannot pull in real credentials.
            # load_dotenv() does not override variables that are already set.
            "TELEGRAM_BOT_TOKEN": "",
            "TELEGRAM_CHAT_ID": "",
            "API_FOOTBALL_KEY": "",
            "PYTHONUNBUFFERED": "1",
        })

        with open(log_path, "w") as log:
            # cwd is the temp dir so the run cannot touch the repo's config_state.json.
            proc = subprocess.Popen([sys.executable, DAEMON], cwd=workdir, env=env,
                                    stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.time() + STARTUP_TIMEOUT
                while time.time() < deadline:
                    if READY in read(log_path):
                        break
                    if proc.poll() is not None:
                        raise AssertionError(
                            f"daemon exited before it was ready (rc={proc.returncode}):\n"
                            f"{read(log_path)[-2000:]}")
                    time.sleep(0.25)
                else:
                    raise AssertionError(
                        f"daemon never logged {READY!r}:\n{read(log_path)[-2000:]}")

                proc.send_signal(signal.SIGTERM)
                started = time.time()
                try:
                    rc = proc.wait(timeout=SHUTDOWN_TIMEOUT)
                except subprocess.TimeoutExpired:
                    raise AssertionError(
                        f"daemon ignored SIGTERM for {SHUTDOWN_TIMEOUT}s -- this is the "
                        f"hang that made systemd SIGKILL it after 90s")
                took = time.time() - started
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()

        output = read(log_path)

        assert rc == 0, (
            f"expected a clean exit, got rc={rc}. A negative rc means the process was "
            f"killed by the signal rather than shutting itself down.\n{output[-2000:]}")
        assert GRACEFUL in output, (
            f"SIGTERM must reach main()'s graceful-shutdown path; {GRACEFUL!r} was never "
            f"logged.\n{output[-2000:]}")

        print(f"PASS: SIGTERM shut the daemon down gracefully in {took:.2f}s (rc={rc})")


main()
