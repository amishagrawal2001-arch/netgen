import requests

# v0.5.361 (audit capture-client-no-timeout): every `requests.*`
# call needs an explicit timeout — pre-fix a wedged capture server
# (deadlocked sniffer, slow disk, network partition) hung the Qt
# slot / worker thread indefinitely. Split into (connect, read):
# connect = 5s (LAN, immediate ACK expected); read = 30s for
# start/stop (dnsmasq / tcpdump spawn is quick) and 60s for
# download (large pcap streaming). Callers already catch
# `requests.RequestException` and surface the error to the UI.
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT_QUICK = 30
_READ_TIMEOUT_DOWNLOAD = 60


class PacketCaptureClient:
    def __init__(self, server_url="http://localhost:5000"):
        self.server_url = server_url

    def start_capture(self, interface: str, filename: str = None):
        """Start a packet capture on the given interface."""
        payload = {
            "interface": interface,
            "filename": filename or f"{interface}_capture.pcap"
        }
        try:
            response = requests.post(
                f"{self.server_url}/api/capture/start",
                json=payload,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT_QUICK),
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e)}

    def stop_capture(self, interface: str):
        """Stop the packet capture on the given interface."""
        try:
            response = requests.post(
                f"{self.server_url}/api/capture/stop",
                json={"interface": interface},
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT_QUICK),
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e)}

    def download_capture(self, filepath: str, save_as: str):
        """Download the .pcap file from the server."""
        # v0.5.361 (audit capture-client-stream-not-closed): use the
        # response as a context manager so the underlying TCP
        # connection is released even when `iter_content` raises
        # (server drops mid-transfer, `open(save_as)` throws on
        # disk-full, etc.). Pre-fix the socket was orphaned on
        # every exception path.
        try:
            with requests.get(
                f"{self.server_url}/api/capture/download",
                params={"filepath": filepath},
                stream=True,
                timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT_DOWNLOAD),
            ) as response:
                response.raise_for_status()
                with open(save_as, "wb") as f:
                    for chunk in response.iter_content(chunk_size=1024):
                        f.write(chunk)
            return {"message": f"Saved to {save_as}"}
        except requests.RequestException as e:
            return {"error": str(e)}
