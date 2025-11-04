import requests

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
            response = requests.post(f"{self.server_url}/api/capture/start", json=payload)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e)}

    def stop_capture(self, interface: str):
        """Stop the packet capture on the given interface."""
        try:
            response = requests.post(f"{self.server_url}/api/capture/stop", json={"interface": interface})
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e)}

    def download_capture(self, filepath: str, save_as: str):
        """Download the .pcap file from the server."""
        try:
            response = requests.get(f"{self.server_url}/api/capture/download", params={"filepath": filepath}, stream=True)
            response.raise_for_status()
            with open(save_as, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024):
                    f.write(chunk)
            return {"message": f"Saved to {save_as}"}
        except requests.RequestException as e:
            return {"error": str(e)}
