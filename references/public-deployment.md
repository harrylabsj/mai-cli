# Local API Deployment Notes

Hosted deployment is deferred for the MVP. For local demos, run the API with SQLite:

```bash
pip install -e '.[api]'
python3 scripts/mai_api.py --db /data/mai-cli.sqlite --host 0.0.0.0 --port 8765
```

Docker Compose runs the same API service and stores SQLite data in a volume:

```bash
docker compose --env-file marketplace.example.env up --build
```

Before any public launch, add TLS, identity, authorization policy, audit logs, backups, monitoring, abuse handling, and formal merchant confirmation workflows.
