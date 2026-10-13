"""Proxy selection shared by backend HTTP callers."""

from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener, proxy_bypass_environment

from ._http_redirects import _CredentialSafeRedirectHandler


def _bypass_proxy(host: str, no_proxy: str) -> bool:
    name = urlsplit("//" + host).hostname
    if name is None:
        return False
    if name.lower().rstrip(".") == "localhost":
        return True
    try:
        address = ip_address(name)
    except ValueError:
        address = None
    if address is not None:
        if address.is_loopback:
            return True
        for entry in no_proxy.split(","):
            try:
                if address in ip_network(entry.strip(), strict=False):
                    return True
            except ValueError:
                pass
    return proxy_bypass_environment(host, {"no": no_proxy})


class _ProxyHandler(ProxyHandler):
    def proxy_open(self, req, proxy, type):
        if req.host and _bypass_proxy(req.host, self.proxies.get("no", "")):
            return None
        return super().proxy_open(req, proxy, type)


def urlopen(request, timeout):
    # Create per request so changes to environment proxy settings take effect.
    return build_opener(_ProxyHandler(), _CredentialSafeRedirectHandler()).open(request, timeout=timeout)
