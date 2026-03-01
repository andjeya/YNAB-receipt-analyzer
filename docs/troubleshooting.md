# Troubleshooting

## Check Pi Service Logs

```bash
sudo systemctl status receipt-shipper
journalctl -u receipt-shipper -n 200 --no-pager
journalctl -u receipt-shipper -f
```

## Re-run Drain Manually

```bash
shipper drain --config /etc/receipt-shipper/shipper.yaml
```

Use this to force outbox processing after maintenance windows.

## Validate SSH + rsync Connectivity

```bash
ssh -o BatchMode=yes -p 22 receipt_ingest@nas.local 'echo ok'
rsync --version
```

Optional shipper transport dry run:

```bash
SHIPPER_RSYNC_DRY_RUN=true shipper once --config /etc/receipt-shipper/shipper.yaml
```

## NAS Down Behavior

When NAS is unavailable:

- shipper logs send failures and keeps files in outbox
- retries follow exponential backoff
- no files are deleted from outbox
- when NAS returns, queue drains automatically

## Outbox Backlog Growing

1. Check NAS reachability and SSH keys.
2. Check free space on Pi (`df -h`) and NAS volume.
3. Confirm `sender.incoming_dir` / `sender.staging_dir` are valid and writable.
4. Run `shipper status --json` to inspect ready vs waiting retry counts.
