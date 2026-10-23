"""URL validation and DNS pinning shared by every scraper transport."""
import asyncio
from dataclasses import dataclass
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


class ScrapeError(Exception):
    def __init__(self, code, message, status_code=400, details=None):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code
        self.details = details or {}


@dataclass(frozen=True)
class Target:
    url: str
    address: str
    host_header: str
    hostname: str
    port: int

    @property
    def pinned_url(self):
        parts = urlsplit(self.url)
        address = '[' + self.address + ']' if ':' in self.address else self.address
        return urlunsplit((parts.scheme, f'{address}:{self.port}', parts.path, parts.query, ''))


class URLPolicy:
    def __init__(self, allow_private=False):
        self.allow_private = allow_private

    async def resolve(self, url):
        try:
            if not isinstance(url, str) or len(url) > 4096 or any(ord(c) < 33 for c in url) or '\\' in url:
                raise ValueError()
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None or parts.password is not None:
                raise ValueError()
            host = parts.hostname.encode('idna').decode('ascii').lower().rstrip('.')
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            if not 1 <= port <= 65535 or '%' in host:
                raise ValueError()
        except (ValueError, UnicodeError):
            raise ScrapeError('INVALID_URL', 'A valid HTTP(S) URL without credentials is required', 422) from None
        if not self.allow_private and (host == 'localhost' or host.endswith(('.localhost', '.local', '.internal'))):
            raise ScrapeError('UNSAFE_URL', 'Only public destinations are permitted')
        try:
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                rows = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
                addresses = list(dict.fromkeys(row[4][0] for row in rows))
            except (OSError, UnicodeError):
                raise ScrapeError('DNS_ERROR', 'Destination DNS resolution failed', 502) from None
        if not addresses:
            raise ScrapeError('DNS_ERROR', 'Destination DNS returned no addresses', 502)
        for address in addresses:
            ip = ipaddress.ip_address(address)
            mapped = getattr(ip, 'ipv4_mapped', None)
            if not self.allow_private and (not ip.is_global or (mapped and not mapped.is_global)):
                raise ScrapeError('UNSAFE_URL', 'Only public destination addresses are permitted')
        authority = '[' + host + ']' if ':' in host else host
        if port != (443 if parts.scheme == 'https' else 80):
            authority += f':{port}'
        normalized = urlunsplit((parts.scheme, authority, parts.path or '/', parts.query, ''))
        return Target(normalized, addresses[0], authority, host, port)
