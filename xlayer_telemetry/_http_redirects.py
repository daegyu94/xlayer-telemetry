"""Private redirect policy for requests carrying backend credentials."""
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler


def _origin(url):
    parts = urlsplit(url)
    return (parts.scheme.lower(), parts.hostname,
            parts.port if parts.port is not None else {"http": 80, "https": 443}.get(parts.scheme.lower()))


class _CredentialSafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        authenticated = any(name.lower() in {"authorization", "proxy-authorization"}
                            for name, _ in req.header_items())
        try:
            same_origin = not authenticated or _origin(req.full_url) == _origin(newurl)
        except ValueError:
            same_origin = False
        if not same_origin:
            # Do not forward credentials or accept another origin's metrics.
            # Omit both URLs and response headers from the generated error.
            fp.close()
            raise HTTPError("", code, "Authenticated redirect origin rejected", None, None) from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)
