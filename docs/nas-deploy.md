# NAS Deployment Notes (Synology)

Production deployment artifacts live under `infra/nas`.

## Current Scope

- `infra/nas/docker-compose.yml`: placeholder stack skeleton
- `infra/nas/.env.example`: baseline env template
- `infra/nas/README.md`: deployment intent and mapping notes

## Volume Expectations

- `/volume1/receipts/incoming`: finalized files shipped from Pi
- `/volume1/receipts/data`: DB/object-store/log path
- `/volume1/receipts/redis`: redis persistence

## Important Constraint

`incoming` and shipper `staging` dirs should be on the same NAS filesystem so remote `mv` remains atomic.

## Planned Next Steps (future work)

1. Build and publish versioned API/frontend images.
2. Add migration job wiring and health checks.
3. Finalize reverse proxy and TLS ingress configuration.
4. Add backup/restore and monitoring procedures.
