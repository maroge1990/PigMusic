"""HTTPS allowlists with public-address validation at the actual TCP connection.

DNS is resolved once per connection and the chosen validated address is passed
as an IP to the socket backend. TLS still authenticates the original hostname.
Proxies and environment transport overrides are disabled. Redirects are checked
before every request, including extractor manifests and fragments.
"""

from contextlib import closing
import ipaddress
import json
import socket
import time
import threading
from urllib.parse import urlsplit

import httpcore
import httpx

from .contracts import ProviderError


_DNS_SLOTS = threading.BoundedSemaphore(4)


def resolve(host, port, timeout):
    # libc DNS does not honour socket timeouts. Bound caller wait and the number
    # of resolver threads; stalled DNS cannot grow threads or block shutdown.
    timeout = min(timeout or 10, 10)
    started = time.monotonic()
    if not _DNS_SLOTS.acquire(timeout=timeout):
        raise ProviderError("unavailable")
    done, result = threading.Event(), []

    def lookup():
        try:
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            result.append(None)
        finally:
            _DNS_SLOTS.release()
            done.set()

    threading.Thread(target=lookup, daemon=True).start()
    if not done.wait(max(0, timeout - (time.monotonic() - started))) or not result[0]:
        raise ProviderError("unavailable")
    return result[0]


class PublicBackend(httpcore.SyncBackend):
    def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        if port != 443:
            raise ProviderError("network_policy")
        addresses = resolve(host, port, timeout)
        ips = list(dict.fromkeys(item[4][0] for item in addresses))
        if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
            raise ProviderError("network_policy")
        # Connecting to a numeric address cannot perform a second hostname lookup.
        return super().connect_tcp(ips[0], port, timeout, local_address, socket_options)


def validate_url(url, hosts):
    try:
        value = urlsplit(url)
        host = (value.hostname or "").lower()
        if (
            value.scheme != "https"
            or value.port not in (None, 443)
            or value.username is not None
            or value.password is not None
            or not host
            or host.endswith(".")
            or not any(host == h or host.endswith("." + h) for h in hosts)
        ):
            raise ProviderError("network_policy")
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ProviderError):
            raise
        raise ProviderError("network_policy") from exc
    return url


def client():
    transport = httpx.HTTPTransport(retries=0)
    # This integration is pinned to httpx 0.28.1 / httpcore 1.0.9 and tested.
    transport._pool._network_backend = PublicBackend()
    return httpx.Client(
        transport=transport, trust_env=False, timeout=10, follow_redirects=False
    )


def stream(client, method, url, hosts, context, *, headers=None, content=None):
    for _ in range(5):
        validate_url(str(url), hosts)
        context.remaining()
        request = client.build_request(
            method,
            url,
            headers=headers,
            content=content,
            timeout=min(10, context.remaining()),
        )
        response = client.send(request, stream=True)
        if response.is_redirect:
            next_url = str(response.url.join(response.headers.get("location", "")))
            response.close()
            # Never carry credentials across origins. Extractor cookie handling
            # is origin scoped, and subsequent hops receive no explicit secrets.
            if urlsplit(next_url).netloc != urlsplit(str(url)).netloc:
                headers = {
                    k: v
                    for k, v in (headers or {}).items()
                    if k.lower() not in {"cookie", "authorization", "host"}
                }
            if (
                response.status_code == 303
                or response.status_code in {301, 302}
                and method == "POST"
            ):
                method, content = "GET", None
            url = next_url
            continue
        return response
    raise ProviderError("invalid_response")


def status_error(status):
    if status in {401, 403, 429}:
        return ProviderError("access_required" if status != 429 else "unavailable")
    return ProviderError("protected" if status in {404, 410, 451} else "unavailable")


def get_json(url, hosts, context):
    # A single retry for transient gateway/transport failure, within one deadline.
    for attempt in range(2):
        try:
            with client() as session:
                with closing(
                    stream(
                        session,
                        "GET",
                        url,
                        hosts,
                        context,
                        headers={
                            "Accept": "application/json",
                            "User-Agent": "PigMusic/1",
                        },
                    )
                ) as response:
                    if response.status_code >= 400:
                        raise status_error(response.status_code)
                    data = bytearray()
                    for chunk in response.iter_bytes(65536):
                        context.remaining()
                        data.extend(chunk)
                        if len(data) > 2_000_000:
                            raise ProviderError("limit")
                    try:
                        return json.loads(data)
                    except (ValueError, UnicodeError) as exc:
                        raise ProviderError("invalid_response") from exc
        except (httpx.HTTPError, ProviderError) as exc:
            failure = exc if isinstance(exc, ProviderError) else ProviderError()
            if not failure.transient or attempt:
                raise failure from exc
            context.remaining()
            time.sleep(min(0.2, context.remaining()))


def transfer(url, hosts, path, context):
    try:
        with client() as session:
            with closing(stream(session, "GET", url, hosts, context)) as response:
                if response.status_code >= 400:
                    raise status_error(response.status_code)
                expected = int(response.headers.get("content-length", 0))
                if expected < 0 or expected > context.max_bytes:
                    raise ProviderError("limit")
                size = 0
                with path.open("xb") as output:
                    for chunk in response.iter_bytes(65536):
                        context.remaining()
                        size += len(chunk)
                        if size > context.max_bytes:
                            raise ProviderError("limit")
                        output.write(chunk)
                        context.progress(size, expected)
                if not size or expected and size != expected:
                    raise ProviderError("invalid_response")
    except httpx.HTTPError as exc:
        raise ProviderError() from exc
