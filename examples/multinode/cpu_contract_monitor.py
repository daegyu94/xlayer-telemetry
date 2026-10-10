"""Owned test-only monitoring container; loopback exposure is selected by Docker."""
from pathlib import Path
import signal
import subprocess
import sys
import time

tools,output=map(Path,sys.argv[1:])
processes=[]
def stop(*args):
    raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
try:
    processes.append(subprocess.Popen([str(tools/'prometheus-3.5.0.linux-amd64/prometheus'),
        '--config.file='+str(output/'monitor-config/prometheus.yml'),'--storage.tsdb.path='+str(output/'prometheus-data'),
        '--web.listen-address=0.0.0.0:19090']))
    processes.append(subprocess.Popen([str(tools/'loki-linux-amd64'),'-config.file='+str(output/'monitor-config/loki.yaml')]))
    while all(process.poll() is None for process in processes): time.sleep(.2)
finally:
    for process in processes:
        if process.poll() is None: process.terminate()
    for process in processes:
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill();process.wait()
