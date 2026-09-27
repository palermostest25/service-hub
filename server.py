import http.client
import json
import os
import re
import ssl
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/opt/service-hub/data"))
SERVICES_FILE = DATA_DIR / "services.json"
PORT = int(os.environ.get("PORT", "1000"))
PROBE_TIMEOUT = 0.45

KNOWN = {
    8787: {"name": "Relay Console", "description": "Arduino relay and power-button control", "kind": "power", "preferred": "http"},
    9000: {"name": "DezKVM", "description": "Remote keyboard, video and mouse", "kind": "kvm", "preferred": "https"},
    8006: {"name": "Proxmox VE", "description": "Virtualization host management", "kind": "server", "preferred": "https"},
}

HIDDEN_PORTS = {22, 25, 85, 111, 1000, 3128, 3300, 3493, 6789}
TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
TAG_RE = re.compile(rb"<[^>]+>")

store_lock = threading.RLock()
cache_lock = threading.RLock()
cache = {"at": 0.0, "services": []}


def load_manual():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not SERVICES_FILE.exists():
        SERVICES_FILE.write_text("[]\n")
        return []
    try:
        data = json.loads(SERVICES_FILE.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_manual(services):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp = SERVICES_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(services, indent=2) + "\n")
    temp.replace(SERVICES_FILE)


def listening_ports():
    ports = set()
    for filename in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(filename).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":
                continue
            try:
                ports.add(int(fields[1].split(":")[1], 16))
            except (ValueError, IndexError):
                continue
    return sorted(port for port in ports if port not in HIDDEN_PORTS)


def fetch_local(port, scheme, path="/"):
    connection_cls = http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    kwargs = {"timeout": PROBE_TIMEOUT}
    if scheme == "https":
        kwargs["context"] = ssl._create_unverified_context()
    connection = connection_cls("127.0.0.1", port, **kwargs)
    try:
        connection.request("GET", path, headers={"Host": "localhost", "User-Agent": "ServiceHub/1.0"})
        response = connection.getresponse()
        body = response.read(65536)
        location = response.getheader("Location")
        return response.status, dict(response.getheaders()), body, location
    finally:
        connection.close()


def title_from(body):
    match = TITLE_RE.search(body)
    if not match:
        return None
    text = TAG_RE.sub(b"", match.group(1)).decode("utf-8", errors="replace")
    return " ".join(text.split())[:80] or None


def probe_port(port):
    known = KNOWN.get(port, {})
    order = [known.get("preferred", "http")]
    order.append("https" if order[0] == "http" else "http")
    for scheme in order:
        try:
            status, headers, body, location = fetch_local(port, scheme)
            if scheme == "http" and status == 400 and b"https" in body.lower():
                # Many TLS-only services explain this over plain HTTP; retry as HTTPS.
                continue
            if status in (301, 302, 303, 307, 308) and location and location.startswith("/"):
                try:
                    _, _, redirected, _ = fetch_local(port, scheme, location)
                    body += redirected
                except Exception:
                    pass
            title = title_from(body)
            if not known and not title and status >= 500:
                continue
            lowered = (title or "").lower() + " " + body[:4096].decode("utf-8", errors="ignore").lower()
            if "dezkvm" in lowered:
                known = {"name": "DezKVM", "description": "Remote keyboard, video and mouse", "kind": "kvm"}
            elif "relay console" in lowered:
                known = {"name": "Relay Console", "description": "Arduino relay and power-button control", "kind": "power"}
            elif "proxmox" in lowered:
                known = {"name": "Proxmox VE", "description": "Virtualization host management", "kind": "server"}
            return {
                "id": f"discovered-{port}",
                "name": known.get("name") or title or f"Unknown service on port {port}",
                "description": known.get("description") or f"Automatically discovered on port {port}",
                "url": f"{scheme}://{{host}}:{port}/",
                "port": port,
                "kind": known.get("kind", "web"),
                "source": "discovered",
                "online": True,
                "status_code": status,
            }
        except Exception:
            continue
    return None


def probe_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    kwargs = {"timeout": PROBE_TIMEOUT}
    if parsed.scheme == "https":
        kwargs["context"] = ssl._create_unverified_context()
    try:
        connection = connection_cls(parsed.hostname, port, **kwargs)
        connection.request("HEAD", parsed.path or "/")
        connection.getresponse().read()
        connection.close()
        return True
    except Exception:
        return False


def discover_services(force=False):
    with cache_lock:
        if not force and time.monotonic() - cache["at"] < 10:
            return list(cache["services"])

    found = []
    ports = set(listening_ports()) | {8787, 9000}
    for port in sorted(ports):
        if port == PORT or port in HIDDEN_PORTS:
            continue
        service = probe_port(port)
        if service:
            found.append(service)
        elif port in KNOWN:
            known = KNOWN[port]
            found.append({
                "id": f"discovered-{port}",
                "name": known["name"],
                "description": known["description"],
                "url": f"{known['preferred']}://{{host}}:{port}/",
                "port": port,
                "kind": known["kind"],
                "source": "discovered",
                "online": False,
            })

    with cache_lock:
        cache["at"] = time.monotonic()
        cache["services"] = found
    return list(found)


def combined_services(host):
    discovered = discover_services()
    result = [{**item, "url": item["url"].replace("{host}", host)} for item in discovered]
    discovered_urls = {item["url"].rstrip("/") for item in result}
    with store_lock:
        manual = load_manual()
    for item in manual:
        if item.get("url", "").rstrip("/") in discovered_urls:
            continue
        result.append({**item, "source": "manual", "online": probe_url(item["url"])})
    result.sort(key=lambda item: (not item.get("online", False), item.get("name", "").lower()))
    return result


class Handler(BaseHTTPRequestHandler):
    server_version = "ServiceHub/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def json_response(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = min(int(self.headers.get("Content-Length", "0")), 65536)
        return json.loads(self.rfile.read(length) or b"{}")

    def host_name(self):
        host = self.headers.get("Host", "localhost").split(":", 1)[0]
        return host.strip("[]") or "localhost"

    def serve_file(self, filename, content_type):
        path = STATIC_DIR / filename
        if not path.is_file():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self.serve_file("index.html", "text/html; charset=utf-8")
        if path == "/static/styles.css":
            return self.serve_file("styles.css", "text/css; charset=utf-8")
        if path == "/static/app.js":
            return self.serve_file("app.js", "text/javascript; charset=utf-8")
        if path == "/favicon.svg":
            return self.serve_file("favicon.svg", "image/svg+xml")
        if path == "/api/services":
            return self.json_response({"services": combined_services(self.host_name()), "scanned_at": time.time()})
        if path == "/api/refresh":
            discover_services(force=True)
            return self.json_response({"services": combined_services(self.host_name()), "scanned_at": time.time()})
        if path == "/healthz":
            return self.json_response({"service": "ok"})
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/services":
            return self.send_error(404)
        try:
            body = self.read_json()
            name = str(body.get("name", "")).strip()[:60]
            url = str(body.get("url", "")).strip()[:300]
            description = str(body.get("description", "")).strip()[:120]
            parsed = urlparse(url)
            if not name or parsed.scheme not in ("http", "https") or not parsed.netloc:
                return self.json_response({"error": "Enter a name and a valid http:// or https:// URL"}, 400)
            item = {"id": str(uuid.uuid4()), "name": name, "url": url, "description": description or "Custom service", "kind": "custom"}
            with store_lock:
                services = load_manual()
                services.append(item)
                save_manual(services)
            return self.json_response(item, 201)
        except (ValueError, json.JSONDecodeError):
            return self.json_response({"error": "Invalid service details"}, 400)

    def do_DELETE(self):
        prefix = "/api/services/"
        if not self.path.startswith(prefix):
            return self.send_error(404)
        service_id = self.path[len(prefix):]
        with store_lock:
            services = load_manual()
            updated = [item for item in services if item.get("id") != service_id]
            if len(updated) == len(services):
                return self.json_response({"error": "Service not found"}, 404)
            save_manual(updated)
        return self.json_response({"deleted": True})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
