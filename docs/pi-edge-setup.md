# Raspberry Pi Edge Setup

This setup configures the Pi as the receipt edge ingestion node.

## 1) Create local folders

```bash
mkdir -p ~/receipts/inbox_scanner ~/receipts/inbox_dropbox ~/receipts/outbox ~/receipts/sent ~/receipts/state
```

- Configure ScanSnap output to `~/receipts/inbox_scanner`.
- Configure Dropbox sync target to `~/receipts/inbox_dropbox`.

## 2) Install shipper

Requires **Python 3.11+** (`python3 --version`). Raspberry Pi OS Bookworm (2023+) ships
3.11. If you are on Bullseye, upgrade the OS or install Python 3.11 via `pyenv` before
continuing.

```bash
cd /path/to/repo
python3 -m venv edge/pi-outbox-shipper/.venv
source edge/pi-outbox-shipper/.venv/bin/activate
pip install --upgrade pip
pip install -e edge/pi-outbox-shipper
sudo mkdir -p /etc/receipt-shipper
sudo cp edge/pi-outbox-shipper/config/shipper.example.yaml /etc/receipt-shipper/shipper.yaml
```

Edit `/etc/receipt-shipper/shipper.yaml` with your inbox paths, NAS host/user/path, and SSH key path.

## 3) Configure SSH key auth to NAS

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -C "receipt-shipper"
ssh-copy-id -p 22 receipt_ingest@nas.local
ssh -p 22 receipt_ingest@nas.local 'mkdir -p /volume1/receipts/incoming /volume1/receipts/staging'
```

Validate non-interactive access:

```bash
ssh -o BatchMode=yes -p 22 receipt_ingest@nas.local 'echo ok'
```

## 4) Install systemd service

```bash
sudo edge/pi-outbox-shipper/scripts/install_systemd.sh
sudo systemctl start receipt-shipper
sudo systemctl status receipt-shipper
```

## 5) Basic verification

```bash
shipper status --config /etc/receipt-shipper/shipper.yaml
shipper drain --config /etc/receipt-shipper/shipper.yaml
journalctl -u receipt-shipper -f
```

**Note on `shipper drain`:** drain exits once inbox and outbox are both empty for
`--max-idle-cycles` consecutive cycles. If you have a slow `stability.stable_seconds`
setting (e.g. 30 s), new files may not yet be enqueued when drain runs its idle check.
For automation scripts, either use a larger `--max-idle-cycles` or add a brief pre-wait
equal to your stability window before calling drain.
