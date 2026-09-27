# Service Hub

A lightweight local launchpad that discovers HTTP and HTTPS services listening on a Linux host. It runs on port `1000`, shows live availability, and provides persistent manually added shortcuts.

## Features

- Automatically recognises Relay Console, DezKVM, and Proxmox VE.
- Shows other responders using their HTML title or `Unknown service on port ####`.
- Searches and rescans from the browser.
- Adds and removes custom links for services on this or another host.
- Stores custom services in `data/services.json`.
- Uses only the Python standard library.
- Runs as an unprivileged system user with only `CAP_NET_BIND_SERVICE` for port 1000.

Discovery only performs short `GET` requests against local listening ports. It never signs in or submits actions to discovered services.

## Install on Linux

```bash
sudo install -d -m 0755 /opt/service-hub
sudo cp -R server.py static service-hub.service /opt/service-hub/
sudo useradd --system --home-dir /opt/service-hub --shell /usr/sbin/nologin --user-group servicehub
sudo install -d -o servicehub -g servicehub -m 0750 /opt/service-hub/data
sudo install -m 0644 service-hub.service /etc/systemd/system/service-hub.service
sudo systemctl daemon-reload
sudo systemctl enable --now service-hub.service
```

Open `http://SERVER-IP:1000`.

## Files

- `server.py` — discovery, custom-service API, and static-file server
- `static/` — responsive web interface
- `service-hub.service` — hardened systemd unit
