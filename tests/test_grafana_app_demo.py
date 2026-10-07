"""The optional App demo must not mistake another service for its owned stack."""
from pathlib import Path
import socket
import subprocess
import sys

def test_grafana_demo_rejects_occupied_port_before_creating_state(tmp_path):
    script=Path(__file__).resolve().parents[1]/'grafana/xlayer-app/scripts/live_demo.py'
    output=tmp_path/'new-state'
    with socket.socket() as existing:
        existing.bind(('127.0.0.1',0));existing.listen()
        result=subprocess.run([sys.executable,str(script),'--tools',str(tmp_path/'tools'),
                               '--output',str(output),'--grafana-port',str(existing.getsockname()[1])],
                              capture_output=True,text=True,timeout=10)
        assert result.returncode==2
        assert 'port already in use' in result.stderr
        assert not output.exists()
        assert existing.getsockname()[1]>0
