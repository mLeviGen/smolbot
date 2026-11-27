# SmolBot

SmolBot 0.1.0 is a private Telegram bot for infrastructure managed by [SmolNugget](https://github.com/mLeviGen/smolnugget). It uses long polling, runs without a public web endpoint, and supports a local SNT installation plus additional SSH hosts.

- Browse hosts, system metrics, SNT processes and bounded log tails.
- Request Start, Stop, Restart or Update with an expiring, one-use confirmation.
- Receive infrastructure, application and resource alerts. Mute alerts or toggle categories per user.
- Keep preferences, latest observations, event history and operation outcomes in SQLite.

## Run

Python 3.12 on Ubuntu is the deployment target. Set up [.env.example](.env.example) and [hosts.example.toml](hosts.example.toml) as described in the deployment guide.

```bash
python -m pip install -r requirements.txt
python main.py --check-config
python main.py
```

The Telegram menu is available via `/start`. Use `/help` for command shortcuts. A process marked Running has an observed PID. Check application-specific health separately.

| Document | Contents |
|---|---|
| [Deployment](docs/DEPLOYMENT.md) | First deployment through SNT, updates, second host and reboot |
| [Architecture](docs/ARCHITECTURE.md) | Components, polling, state and operation lifecycle |
| [Security](docs/SECURITY.md) | Permissions, secrets, audit and trust boundaries |
| [SNT integration](docs/SNT_INTEGRATION.md) | Verified SNT commands and schema interpretation |
| [SNT findings](docs/SNT_FINDINGS.md) | Upstream limitations and potential improvements |
| [Testing](docs/TESTING.md) | Automated checks and manual acceptance |

License selection is pending owner approval.
