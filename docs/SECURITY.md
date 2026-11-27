# Security

SmolBot checks Telegram numeric User IDs and private-chat identity on inbound messages and callback queries. Missing or empty allowlists stop startup. Notifications target only configured numeric IDs with delivery preferences enabled. Start the bot privately from each account that should receive alerts. Keep the Telegram bot out of groups.

Callbacks contain opaque tokens with server-side routes. Tokens are user-bound and expire. Operation confirmations have their own 60-second expiry and are consumed once before submitting the validated action. Name validation, fresh SNT membership checks and the configured protected names apply at the SNT boundary. The default protected name `smolbot` is always enforced. Register under that name or add every alias to `PROTECTED_APP_NAMES`.

The local transport uses argument vectors. SSH uses OpenSSH in batch mode with dedicated identity files, explicit known hosts and strict host-key verification. Arguments are individually POSIX-quoted before transmission. The only SSH metrics payload is a fixed Python program. SQLite binds user-supplied values in SQL parameters. Telegram messages use plain text.

Run as the same unprivileged account as SNT. That grants the bot the privileges of that account, including access to its SNT processes and files. A compromise of SmolBot or a Telegram account on the allowlist can have infrastructure impact. Keep config and keys owner-readable. Use separate SSH key pairs for SmolBot and trust host fingerprints obtained via a separate trusted channel. Set up one owner-controlled bot process per database.

Only bounded tails are read. Log output over 128 KiB is refused and smaller long results can be sent as a text file. The bot masks the configured bot token, explicit values in `LOG_REDACT_VALUES`, and common token, credential, and private-key formats. Arbitrary application output can contain unknown or fragmented secrets that pattern matching will miss. Telegram messages and uploaded logs are visible in the Telegram account and may be retained there. Applications should avoid writing secrets to logs.

The operation journal records identity, target, action, phase and timestamps. Ordinary error logs avoid command stderr, tokens and environment contents. A local subprocess timeout terminates the child process group on Linux. An SSH timeout ends the local client. A previously submitted remote command may continue server-side.
