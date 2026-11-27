# First deployment through SmolNugget

Use the repository revision approved in independent review. Run as `levi` on `smol-host-001`. Confirm Python 3.12, the SNT executable path and the correct SNT home with `python3 --version`, `command -v snt` and `snt status --json`. The SNT service must run under the same user and use the same home if it is overridden.

## Prepare configuration before registration

```bash
umask 077
install -d -m 700 "$HOME/.config/smolbot"
chmod 700 "$HOME/.config/smolbot"
touch "$HOME/.config/smolbot/.env" "$HOME/.config/smolbot/hosts.toml"
chmod 600 "$HOME/.config/smolbot/.env" "$HOME/.config/smolbot/hosts.toml"
```

Open the files with your editor, use [.env.example](../.env.example) as reference and put the real BotFather token in `BOT_TOKEN`. Obtain your numeric ID from a trusted Telegram ID lookup or by inspecting `message.from.id` in your bot’s private Telegram Bot API `getUpdates` response before polling starts. Use a private environment and keep the token out of shell history and screenshots. List authorized IDs in `ALLOWED_USER_IDS`.

Set `HOSTS_CONFIG=hosts.toml` and `DATABASE_PATH=data/smolbot.db`. SmolBot resolves those paths relative to the env file. Start with a single entry in `hosts.toml`:

```toml
[[hosts]]
id = "host-001"
name = "smol-host-001"
transport = "local"
snt_path = "/home/levi/.local/bin/snt"
```

If the SNT service uses a custom home, add `snt_home` with that absolute path. Keep `.env` and `hosts.toml` mode 600. The database and configuration are outside the checkout and survive updates.

## Register and verify

```bash
snt add https://github.com/mLeviGen/smolbot smolbot -c "python main.py" \
  --venv -e "SMOLBOT_ENV_FILE=$HOME/.config/smolbot/.env"
snt status smolbot --json
snt logs smolbot -n 50
```

SNT starts a newly registered app when its daemon is already active. The configuration is ready by this point. `snt sync smolbot` is only for preparing a registered app that does not hold a worker lock. If correcting a bad configuration after registration, use `snt stop smolbot`, edit the files, then `snt start smolbot`. Test `/start` from the authorized private chat.

For subsequent updates, use `snt update smolbot` or wait for SNT’s normal check interval. Verify the running `commit` against the approved GitHub SHA, inspect the log, and confirm your Telegram settings persisted. A changed `checkout` alone only confirms the checkout moved.

## Add another Linux host

Create a dedicated Ed25519 key pair on the machine running SmolBot. Install only its public key for the unprivileged account that owns SNT on the remote machine. Compare the new server’s host key fingerprint with a fingerprint obtained through its trusted console and enroll the verified public host key in the configured `known_hosts` file. Keep the private key mode 600. Test `snt status --json` using the same account, identity and strict host-key checking before enabling the SSH entry in [hosts.example.toml](../hosts.example.toml). The fixed metrics probe uses the remote `python3` on PATH and Linux procfs.

Restart SmolBot with `snt restart smolbot` from the shell after editing its config.

## Reboot recovery

Use `systemctl --user status smolnugget.service` and `loginctl show-user "$USER" -p Linger` to verify the existing user service and startup without an interactive login. If lingering is disabled, your system administrator can enable it once. After a permitted reboot, check SNT status and logs and verify the bot accepts private commands and preserves preferences.
