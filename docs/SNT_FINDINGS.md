# SmolNugget findings

- Status schema 1 exposes process state and recent errors. Add an operation ID and a status record for each request to make completion, rejection and no-op updates distinguishable.
- Restart and Update use a single control file per app. Independent callers can race or replace queued requests. A serialized queue and per-request acknowledgement would improve control reliability.
- `snt remote add` can hide the concrete SSH failure behind a generic exit-code explanation. This complicates diagnosis of SSH Agent configuration and passphrase-protected keys. Sanitized cause categories or an opt-in diagnostic mode would help.
- `snt add` registers an enabled app. The active daemon can launch it immediately, including with `--no-clone`. Adding a supported disabled-registration option would simplify first deployments.
- Status reports running commit and checkout separately. Setup failure may leave the previous process alive. An explicit setup result field would help distinguish pending and failed updates.
- The SNT dashboard can expose control without application-level authentication if bound to a public interface. Keep it loopback-only and use an SSH tunnel when needed.
