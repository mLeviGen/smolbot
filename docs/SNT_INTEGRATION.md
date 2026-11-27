# SmolNugget integration

Reviewed SmolNugget 2.1.0 at `7c5ee7838b81b2f8d4cb2e5644b5f81acddaf445`. Sources inspected include the README, CLI, remote, sysinfo, state, webui, config, supervisor and the associated tests.

| Interface | Use |
|---|---|
| `snt [--home PATH] status --json` | Read `schema`, `smolnugget`, `daemon` and `apps`. |
| `snt start NAME`, `snt stop NAME` | Enable or disable the app via the configuration SNT owns. |
| `snt restart NAME`, `snt update NAME` | Ask the managing SNT worker to restart or check for updates. |
| `snt logs NAME -n COUNT` | Print the bounded log tail. |
| `snt add … -c "python main.py"` | Register SmolBot. SNT installs its requirements into a virtualenv. |
| `snt sync NAME` | Check out and prepare a registered app when its worker lock is free. |

Schema 1 entries contain `name`, `status`, `enabled`, `pid`, `started_at`, `uptime_seconds`, `restarts`, `commit`, `checkout`, `last_check`, `last_error`, `restart_policy` and `check_interval`. The daemon provides `running`, `pid` and `started_at`. SmolBot accepts unknown fields, displays missing optional values as N/A and rejects incompatible schemas. Daemon availability and host availability are tracked separately. The enabled flag determines whether a stopped app should be considered for outage alerts.

SmolBot submits on the remote host with OpenSSH directly. SNT’s `-r` also uses SSH with shell-quoted arguments and a remote `PATH` change. SmolBot configures a deterministic explicit SNT executable and independently selects host-key policy, timeouts and identity. The SNT web dashboard serves `/api/system` via `SystemInfo`, independent of status JSON. SmolBot gets equivalent core metrics through psutil and a fixed Linux SSH probe. The disk figures refer to the root filesystem on Linux.

A successful Start or Stop can mean only an enabled flag change has been requested. Restart and Update write a control file. CLI waits up to five seconds to observe consumption and adds `(request queued)` if still present. Consumption confirms that the managing process read the request. SmolBot verifies enabled/running or disabled state, process identity change, or observed running commit plus update check for the corresponding action. A new SNT error during observation is recorded as a reported failure. Independent SNT work can change the same fields concurrently. Unchanged-commit updates and indistinguishable transitions expire as unconfirmed.
