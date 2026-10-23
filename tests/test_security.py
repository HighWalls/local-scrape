import socket

import pytest


@pytest.mark.asyncio
async def test_public_policy_blocks_internal_literals_and_mixed_dns(monkeypatch):
    from local_scrape.security import URLPolicy, ScrapeError

    policy = URLPolicy()
    for url in ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/", "file:///secret", "http://user:pass@example.com/", "http://10.0.0.1/", "http://[::ffff:127.0.0.1]/"]:
        with pytest.raises(ScrapeError):
            await policy.resolve(url)

    async def mixed(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)), (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))]

    monkeypatch.setattr("asyncio.base_events.BaseEventLoop.getaddrinfo", mixed)
    with pytest.raises(ScrapeError, match="public"):
        await policy.resolve("http://mixed.example/")


@pytest.mark.asyncio
async def test_public_address_pinned_and_private_requires_config():
    from local_scrape.security import URLPolicy

    target = await URLPolicy().resolve("https://93.184.216.34/a?q=1#x")
    assert target.url == "https://93.184.216.34/a?q=1"
    assert target.address == "93.184.216.34"
    private = await URLPolicy(allow_private=True).resolve("http://127.0.0.1:8123/")
    assert private.host_header == "127.0.0.1:8123"
