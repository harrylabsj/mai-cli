# Local API Deployment Notes

Hosted deployment is deferred for the MVP. For local demos, run the API with SQLite:

```bash
pip install -e '.[api]'
export MAI_ADMIN_TOKEN='replace-with-a-long-random-secret'
export MAI_CHANNEL_TOKENS='telegram:replace-with-channel-secret'
python3 scripts/mai_api.py --db /data/mai-cli.sqlite --host 0.0.0.0 --port 8765
```

Docker Compose runs the same API service and stores SQLite data in a volume:

```bash
docker compose --env-file marketplace.example.env up --build
```

`MAI_ADMIN_TOKEN` is required for API merchant onboarding. Channel ingress through `/channels/messages` is disabled unless `MAI_CHANNEL_TOKENS` or `MAI_CHANNEL_TOKEN` is configured.

Before any public launch, add TLS, identity, authorization policy, audit logs, backups, monitoring, abuse handling, and formal merchant confirmation workflows.
