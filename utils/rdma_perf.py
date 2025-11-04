import subprocess
import threading

perf_stats = {}

def start_ibperf_server(stream_data, stop_event):
    interface = stream_data.get("interface")
    iteration = stream_data.get("ibperf_iteration", 1000)
    mtu = stream_data.get("frame_size", 65536)
    rate_limit = stream_data.get("ibperf_rate_limit", 100_000)
    direction = stream_data.get("ibperf_direction", 2)

    cmd = [
        "ib_write_bw", "-d", "mlx5_0", "-F", "-m", str(mtu),
        "-s", "32K", "-x", "1", "-D", "100", "-q", "1", "--report_gbits",
        "-n", str(iteration), "--rate_limit=" + str(rate_limit)
    ]
    if direction == 2:
        cmd.append("-b")

    def run_ibperf():
        global perf_stats
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

        for line in proc.stdout:
            logging.info(f"[ibperf] {line.strip()}")
            if "Gbps" in line:
                # Example line: 0.00-0.10 1234.56 Gbps
                tokens = line.split()
                try:
                    perf_stats[interface] = {
                        "timestamp": time.time(),
                        "rate": float(tokens[-2]),
                        "unit": tokens[-1]
                    }
                except Exception:
                    pass

            if stop_event.is_set():
                proc.terminate()
                break

    threading.Thread(target=run_ibperf, daemon=True).start()
