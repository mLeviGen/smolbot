# Testing and acceptance

```bash
python -m pip install -r requirements-dev.txt
ruff check .
ruff format --check .
python -m pytest -q
python -m unittest discover -s tests
python -m compileall -q smolbot main.py
```

Tests use synthetic configuration, temporary SQLite databases, fake SNT JSON and Telegram/SSH mocks. Local subprocess tests run real short-lived Python commands. The optional SNT smoke works with a checked-out SNT source tree via `SMOLBOT_TEST_SNT_SOURCE=/absolute/path/to/smolnugget`. It runs a temporary SNT home and does not use the owner’s daemon or application registry.

## Manual acceptance

- [ ] Run Python 3.12 through SNT on the intended server. Check initial logs, last successful poll, daemon status and all system metrics.
- [ ] Verify `/start`, `/help`, private menus, application pagination, back, refresh and stale callbacks.
- [ ] Send commands from a second non-allowlisted user and a test group. Confirm there is no access and no notification.
- [ ] Use a disposable app for Start, Stop, Restart and Update. Test cancel, expired confirmation, duplicate click and simultaneous requests. Verify changed PID/commit, and check that unchanged-commit Update is unconfirmed.
- [ ] Try 20, 50, 100 and 200 log lines on a disposable app. Check masking, text file delivery and refusal above 128 KiB.
- [ ] Simulate an outage on a test SSH host. Verify other hosts continue responding, the alert waits for three failed polls and recovery is announced once.
- [ ] Verify threshold samples, recovery, update/setup errors, cooldown, muted alerts, enabled categories and persistence after restart.
- [ ] Reboot at an authorized time, verify the user service and confirm Telegram polling resumes.
- [ ] Observe RSS, CPU, loop delay and poll duration under a representative 10-host/50-app workload. Target under 150 MB RSS with low idle CPU and bounded poll concurrency. A mocked functional workload does not establish production resource usage.
