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
2. Check free space on Pi (`df -h`) and NAS volume (`du -sh ~/receipts/outbox`).
3. Confirm `sender.incoming_dir` / `sender.staging_dir` are valid and writable.
4. Run `shipper status --json` to inspect ready vs waiting retry counts.

## Clean Up Orphan Staging Files on NAS

When a send attempt fails after rsync but before the remote rename completes, a
`.part-*` temp file is left in `sender.staging_dir` on the NAS. These are harmless but
accumulate over a prolonged failure period. Clean them up with:

```bash
ssh -p 22 receipt_ingest@nas.local \
  'find /volume1/receipts/staging -name "*.part-*" -mmin +60 -delete'
```

Check for orphans without deleting first:

```bash
ssh -p 22 receipt_ingest@nas.local \
  'find /volume1/receipts/staging -name "*.part-*" -mmin +60'
```

Adjust the path to match your configured `sender.staging_dir`.

## `shipper drain` Exits Before All Files Are Sent

`drain` counts consecutive cycles where both inbox and outbox are empty. If files in
the inbox are still in their stability window (not yet moved to outbox) when drain
checks, it may count that cycle as idle and exit prematurely.

Workaround: use `--max-idle-cycles` larger than your `stability.stable_seconds /
poll_interval_seconds`, or wait for the stability window to pass before calling drain.
