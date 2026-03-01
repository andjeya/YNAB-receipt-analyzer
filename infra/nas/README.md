# NAS Deployment Placeholder (Synology)

This directory contains the future production compose artifacts for the Synology NAS runtime.

## Current Status

- `docker-compose.yml` is a placeholder skeleton.
- It documents expected services, volume mounts, and network naming.
- It is not yet a finalized production deployment definition.

## Intended Volume Mapping

- `/volume1/receipts/incoming` -> `/app/incoming` in backend container.
- `/volume1/receipts/data` -> `/app/data` for DB/object-store/logs.

Pi outbox shipper delivers files to `/volume1/receipts/incoming` (finalized filenames only).

## How This Will Be Used Later

1. Build/publish versioned API and frontend images.
2. Copy `.env.example` to `.env` and set secrets on NAS.
3. Run migrations before service rollout.
4. Attach reverse-proxy / TLS routing (Synology reverse proxy or external gateway).

## Notes

- Keep `incoming` and staging directories on the same NAS filesystem for atomic rename behavior.
- This placeholder is intentionally separate from the root `docker-compose.yml` used in current dev workflows.
