# Pi Outbox Shipper

`pi-outbox-shipper` is the Raspberry Pi edge ingestion component for receipts.

It moves stable files from local inbox folders (scanner + Dropbox) into a durable local outbox, then reliably ships outbox files to the NAS over `rsync`/SSH using an atomic NAS handoff.

## Flow

```text
inbox_scanner / inbox_dropbox
  -> (stable file check)
  -> outbox (durable queue on Pi)
  -> rsync to NAS staging path
  -> remote atomic move to NAS incoming path
  -> local sent archive (or delete)
```

## Reliability Guarantees

- Durable queue: files are renamed into local outbox before send attempts.
- No-loss retries: failed sends stay in outbox and are retried with exponential backoff.
- Crash tolerance: Pi reboot leaves files in inbox/outbox; daemon resumes from disk state.
- Atomic NAS handoff: backend only reads from final incoming path after remote `mv` from staging.
- Idempotency safety: sent-state tracking plus remote final-file existence check prevents duplicate re-delivery loops.

## Configuration

Primary config file is YAML (example: `config/shipper.example.yaml`), with env overrides.

Key options:

- `paths.inboxes`: one or more source inbox folders (`name`, `path`).
- `paths.outbox`: durable queue folder.
- `paths.sent_archive`: archive path used when `runtime.post_send_action=archive`.
- `paths.state_db`: sqlite state DB path.
- `stability.stable_seconds`: unchanged window before file is considered stable.
- `stability.min_age_seconds`: minimum file age before enqueue.
- `retry.initial_backoff_seconds` / `retry.max_backoff_seconds`.
- `sender.host` / `sender.user` / `sender.port` / `sender.ssh_key`.
- `sender.incoming_dir`: NAS final ingest folder.
- `sender.staging_dir`: NAS temp folder (must be same filesystem as incoming for atomic rename).
- `sender.dry_run`: simulate sends without NAS.
- `sender.rsync_dry_run`: run `rsync --dry-run` transport checks.
- `runtime.post_send_action`: `archive` or `delete`.

Environment overrides (examples):

- `SHIPPER_CONFIG=/etc/receipt-shipper/shipper.yaml`
- `SHIPPER_OUTBOX_DIR=/home/pi/receipts/outbox`
- `SHIPPER_NAS_HOST=nas.local`
- `SHIPPER_NAS_USER=receipt_ingest`
- `SHIPPER_DRY_RUN=true`
- `SHIPPER_INBOXES=scanner=/home/pi/receipts/inbox_scanner,dropbox=/home/pi/receipts/inbox_dropbox`

## Install (venv)

```bash
cd edge/pi-outbox-shipper
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
cp config/shipper.example.yaml /etc/receipt-shipper/shipper.yaml
```

## CLI Commands

```bash
shipper run --config /etc/receipt-shipper/shipper.yaml
shipper once --config /etc/receipt-shipper/shipper.yaml
shipper drain --config /etc/receipt-shipper/shipper.yaml
shipper status --config /etc/receipt-shipper/shipper.yaml
shipper status --config /etc/receipt-shipper/shipper.yaml --json
```

- `run`: daemon loop (recommended for systemd).
- `once`: single scan/send cycle (cron-compatible).
- `drain`: loops until inbox+outbox are empty for consecutive idle cycles.
- `status`: queue and retry counters.

## Systemd

Template unit: `systemd/receipt-shipper.service`

Helper installer:

```bash
sudo edge/pi-outbox-shipper/scripts/install_systemd.sh
```

Operational commands:

```bash
sudo systemctl start receipt-shipper
sudo systemctl stop receipt-shipper
sudo systemctl restart receipt-shipper
sudo systemctl status receipt-shipper
journalctl -u receipt-shipper -f
```
