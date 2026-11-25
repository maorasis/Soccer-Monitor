# Operations Guide: Soccer Monitor on Oracle Cloud

Setup and day-to-day operation of the [Live Under Monitor](live_under_monitor.py) on an Oracle Cloud Always Free VM.

> ⛔ **Never click "Upgrade to Pay As You Go".** An un-upgraded Always Free account cannot be
> charged. Oracle takes a temporary ~$1 card hold at signup; that's expected.

---

## Setup

### 1. Create the account

Sign up at [cloud.oracle.com](https://cloud.oracle.com). Pick your **home region** carefully — it
is permanent and Always Free only works there. `il-jerusalem-1` is a good pick.

Then set a $1 budget alert: **Billing → Cost Management → Budgets**.

### 2. Create the network first

Do this **before** creating the instance. Building the VCN inline from the Create Instance page
leaves the "Assign a public IPv4 address" toggle greyed out and unclickable — the subnet doesn't
exist yet, so the form can't confirm it's public, and there's no way forward.

**Networking → Virtual Cloud Networks → Start VCN Wizard → "VCN with Internet Connectivity"**

Name it, accept every default CIDR, click through, Create. This gives you a VCN, a **public**
subnet, an internet gateway, and the route table wiring.

### 3. Create the instance

**Compute → Instances → Create Instance**

| Setting | Value |
|---|---|
| Shape | `VM.Standard.A1.Flex` — 1 OCPU / 6 GB |
| Image | Ubuntu 24.04 LTS (aarch64) |
| Boot volume | Leave the default (~47 GB) |

If you get **"Out of host capacity"**: retry in a few hours, or switch the shape to
`VM.Standard.E2.1.Micro`.

**Networking section:**

- **Primary network**: "Select existing virtual cloud network" → the VCN from step 2.
  VNIC name can stay blank.
- **Subnet**: "Select existing subnet" → the one labeled **public**.
- **Private IPv4 address**: leave "Automatically assign private IPv4 address".
- **Public IPv4 address assignment**: toggle **ON**. It should be clickable now.
- **IPv6 address assignment**: leave off.

Leave the default Security List / NSG as-is — no firewall or port changes are needed, since the monitor only makes outbound connections.

**Add SSH keys:** paste your public key, or use "Generate a key pair for me" and **download the
private key before clicking Create**. Without it you cannot SSH in afterward.

### 4. Connect

If you used "Generate a key pair for me", Oracle downloaded the private key to `~/Downloads/`
(named like `ssh-key-YYYY-MM-DD.key`). Your own `~/.ssh/id_ed25519` will **not** work — the server
has never seen it. Install Oracle's key instead:

```bash
mv ~/Downloads/ssh-key-*.key ~/.ssh/oracle_polymarket
chmod 600 ~/.ssh/oracle_polymarket
```

Add a shortcut to `~/.ssh/config`:

```
Host polymarket
    HostName <INSTANCE_PUBLIC_IP>
    User ubuntu
    IdentityFile ~/.ssh/oracle_polymarket
```

```bash
ssh polymarket
```

### 5. Give the server access to GitHub

The repo is private, so `git clone` needs credentials on the server. Generate a key there and
register it as a **read-only deploy key**:

```bash
# on the server
ssh-keygen -t ed25519 -N "" -C "oracle-polymarket-deploy" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

Paste that into **GitHub → repo → Settings → Deploy keys → Add deploy key**, leaving "Allow write
access" unchecked.

### 6. Install

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3-venv python3-pip git nano

cd /home/ubuntu
git clone git@github.com:maorasis/Soccer-Monitor.git soccer_monitor
cd soccer_monitor

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

### 7. Copy over secrets and state

`.env`, `config_state.json`, and `live_under_alerts.sqlite3` are local state — `git clone` does not bring them. Stop the old service before copying the database so its alert history and pending rearms are transferred consistently.

Stop the service on the old server, then copy the files from your laptop:

```bash
ssh <olduser>@<OLD_IP> sudo systemctl stop soccer_monitor
scp <olduser>@<OLD_IP>:~/soccer_monitor/{.env,config_state.json,live_under_alerts.sqlite3} .
scp .env config_state.json live_under_alerts.sqlite3 ubuntu@<NEW_IP>:~/soccer_monitor/
```

Starting fresh instead? Create `.env` by hand using `.env.example` as the key list:

```bash
nano /home/ubuntu/soccer_monitor/.env
```

### 8. Stop the old server

Do this before starting the new one — two instances on the same Telegram token will conflict.

```bash
# on the OLD server
sudo systemctl stop soccer_monitor
```

### 9. Install the services

```bash
sudo cp /home/ubuntu/soccer_monitor/soccer_monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now soccer_monitor
sudo systemctl status soccer_monitor
```

### 10. Verify

```bash
journalctl -u soccer_monitor -f -o short-iso        # Minute polls appear during active hours
```

Send `/menu` to your Telegram bot and confirm it responds.

### 11. Delete the old GCP project

Only once everything above passes.

```bash
gcloud projects delete polymarket-dip-monitor
```

---

## Daily Operations

### Logs

```bash
journalctl -u soccer_monitor -f                     # live stream (Ctrl+C to stop)
journalctl -u soccer_monitor -n 100 --no-pager      # last 100 lines
journalctl -u soccer_monitor -f -o short-iso        # with ISO timestamps
journalctl -u soccer_monitor -e                     # jump to the end
```

### Update the code

```bash
cd /home/ubuntu/soccer_monitor
git pull --ff-only
sudo install -m 644 soccer_monitor.service /etc/systemd/system/soccer_monitor.service
sudo systemctl daemon-reload
sudo systemctl restart soccer_monitor
journalctl -u soccer_monitor -f -n 20
```

### Control the daemons

```bash
sudo systemctl status  soccer_monitor
sudo systemctl start   soccer_monitor
sudo systemctl stop    soccer_monitor
sudo systemctl restart soccer_monitor
sudo systemctl enable  soccer_monitor    # launch on boot
sudo systemctl disable soccer_monitor
```

### Edit configuration

Leagues and thresholds are editable live via Telegram `/menu` — no restart needed. For tokens and
chat IDs:

```bash
nano /home/ubuntu/soccer_monitor/.env
sudo systemctl restart soccer_monitor
```

---

## Troubleshooting

**Edited a `.service` file** — reload systemd, or the change is ignored:

```bash
sudo systemctl daemon-reload
sudo systemctl restart soccer_monitor
```

**Telegram HTTP 409 in the logs** — two instances are polling the same bot token. Stop the other one.

**`systemctl stop` hangs for ~90s** — `soccer_monitor` doesn't act on SIGTERM, so systemd waits out
its timeout and then force-kills it. The unit ends up `failed (Result: timeout)` rather than
`inactive`. Expected; the process does die.

**No alerts for days** — check `systemctl status`, the monitoring schedule, enabled leagues, and current limits.

**Instance stopped on its own** — Oracle stops Always Free instances it considers idle. Restart it
from the console.
