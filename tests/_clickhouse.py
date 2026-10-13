"""SQL on an explicitly owned container or loopback HTTP test instance."""
import os
import subprocess
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from xlayer_telemetry.analysis.diagnostics import ThreeFSClient


URL = os.environ.get('XLAYER_TEST_CLICKHOUSE_URL')
CONTAINER = os.environ.get('XLAYER_TEST_CLICKHOUSE_CONTAINER')
AVAILABLE = bool(URL or CONTAINER)


def execute(query, data=None):
    if URL:
        parsed = urlsplit(URL)
        if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('ClickHouse test URL must identify an owned loopback HTTP instance')
        body = (data or '').encode() if data is not None else query.encode()
        target = URL.rstrip('/') + ('/?' + urlencode({'query': query}) if data is not None else '/')
        with urlopen(Request(target, data=body), timeout=15) as response:
            return response.read().decode()
    return subprocess.run(['docker', 'exec', '-i', CONTAINER, 'clickhouse-client',
                           '--multiquery', '--query', query], input=data, capture_output=True,
                          text=True, check=True, timeout=15).stdout


def client(database):
    if URL:
        return ThreeFSClient(URL, database=database)

    class ContainerClient(ThreeFSClient):
        def _query_rows(self, query):
            import json
            return [json.loads(line) for line in execute(query).splitlines()]
    return ContainerClient('http://unused', database=database)
