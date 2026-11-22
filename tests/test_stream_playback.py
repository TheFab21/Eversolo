"""Tests for routing stream URLs from the media player to the DLNA renderer."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")

from homeassistant.exceptions import HomeAssistantError

from custom_components.eversolo import media_player
from custom_components.eversolo.coordinator import (
    EversoloDataUpdateCoordinator,
)
from custom_components.eversolo.dlna import EversoloDlnaError

HOST = "172.22.22.128"
FIP_AAC = "http://icecast.radiofrance.fr/fip-hifi.aac"
FIP_LOGO = "https://example.org/fip.png"


def _player(locations: list[str]) -> media_player.EversoloMediaPlayer:
    """Return a media player whose coordinator knows the given locations."""
    coordinator = MagicMock()
    coordinator.config_entry.entry_id = "entry"
    coordinator.config_entry.title = "DMP-A6"
    coordinator.device_info = {"model": "DMP-A6", "net_mac": "80:0a:80:5f:53:7a"}
    coordinator.client.host = HOST
    coordinator.async_dlna_locations = AsyncMock(return_value=locations)
    coordinator.async_request_refresh = AsyncMock()
    player = media_player.EversoloMediaPlayer(coordinator)
    player.hass = MagicMock()
    return player


@pytest.fixture
def play_stream():
    """Replace the network call and the session lookup."""
    with (
        patch.object(media_player, "async_get_clientsession"),
        patch.object(media_player, "async_play_stream", AsyncMock()) as mocked,
    ):
        yield mocked


async def test_stream_url_goes_to_the_renderer_as_a_station(play_stream) -> None:
    """The title and logo come from extra, under dlna_dmr's own keys."""
    location = f"http://{HOST}:1639/description.xml"
    player = _player([location])

    await player.async_play_media(
        "music", FIP_AAC, extra={"title": "FIP", "thumb": FIP_LOGO}
    )

    play_stream.assert_awaited_once()
    _session, called_location, url, title, logo = play_stream.await_args.args
    assert (called_location, url, title, logo) == (location, FIP_AAC, "FIP", FIP_LOGO)
    player.coordinator.async_request_refresh.assert_awaited_once()
    player.coordinator.client.async_play_library_item.assert_not_called()


async def test_stream_without_extra_is_named_after_its_url(play_stream) -> None:
    """A bare URL still plays, under its own address and without artwork."""
    player = _player([f"http://{HOST}:1639/description.xml"])

    await player.async_play_media("music", FIP_AAC)

    _session, _location, _url, title, logo = play_stream.await_args.args
    assert (title, logo) == (FIP_AAC, None)


async def test_stale_location_falls_through_to_the_next(play_stream) -> None:
    """A port left stale by a restart does not stop the stream."""
    stale = f"http://{HOST}:1429/description.xml"
    current = f"http://{HOST}:1639/description.xml"
    play_stream.side_effect = [EversoloDlnaError("unreachable"), None]
    player = _player([stale, current])

    await player.async_play_media("music", FIP_AAC, extra={"title": "FIP"})

    tried = [call.args[1] for call in play_stream.await_args_list]
    assert tried == [stale, current]


async def test_every_location_failing_reports_each_reason(play_stream) -> None:
    """When nothing answers, the error carries what every attempt said."""
    play_stream.side_effect = [
        EversoloDlnaError("first refused"),
        EversoloDlnaError("second refused"),
    ]
    player = _player(
        [f"http://{HOST}:1429/description.xml", f"http://{HOST}:1639/description.xml"]
    )

    with pytest.raises(HomeAssistantError) as raised:
        await player.async_play_media("music", FIP_AAC)

    assert raised.value.translation_key == "dlna_play_failed"
    assert raised.value.translation_placeholders == {
        "url": FIP_AAC,
        "error": "first refused; second refused",
    }
    player.coordinator.async_request_refresh.assert_not_called()


async def test_undiscovered_renderer_is_reported(play_stream) -> None:
    """Without any known address, the user is told what to check."""
    player = _player([])

    with pytest.raises(HomeAssistantError) as raised:
        await player.async_play_media("music", FIP_AAC)

    assert raised.value.translation_key == "dlna_renderer_not_found"
    assert raised.value.translation_placeholders == {"host": HOST}
    play_stream.assert_not_called()


async def test_library_identifiers_still_use_the_api(play_stream) -> None:
    """Only http and https URLs take the DLNA path."""
    player = _player([f"http://{HOST}:1639/description.xml"])
    player.coordinator.client.async_play_queue_item = AsyncMock()

    await player.async_play_media("music", '{"kind":"queue_track","index":3}')

    player.coordinator.client.async_play_queue_item.assert_awaited_once_with(3)
    play_stream.assert_not_called()


async def test_locations_prefer_ssdp_and_keep_only_this_device() -> None:
    """SSDP comes first, dlna_dmr's stored URL second, other hosts never."""
    fresh = f"http://{HOST}:1639/description.xml"
    stored = f"http://{HOST}:1429/description.xml"
    other = "http://172.22.22.11:8080/description.xml"
    discovered = {
        "urn:schemas-upnp-org:device:MediaRenderer:1": [
            SimpleNamespace(ssdp_location=other),
            SimpleNamespace(ssdp_location=fresh),
        ],
        "urn:schemas-upnp-org:service:AVTransport:1": [
            SimpleNamespace(ssdp_location=fresh),
            SimpleNamespace(ssdp_location=None),
        ],
    }
    fake = SimpleNamespace(
        hass=MagicMock(),
        client=SimpleNamespace(host=HOST),
    )
    fake.hass.config_entries.async_entries.return_value = [
        SimpleNamespace(data={"url": stored}),
        SimpleNamespace(data={"url": other}),
        SimpleNamespace(data={}),
    ]

    async def by_st(_hass, target):
        return discovered[target]

    fake_ssdp = SimpleNamespace(async_get_discovery_info_by_st=by_st)
    with patch.dict(sys.modules, {"homeassistant.components.ssdp": fake_ssdp}):
        locations = await EversoloDataUpdateCoordinator.async_dlna_locations(fake)

    assert locations == [fresh, stored]
    fake.hass.config_entries.async_entries.assert_called_once_with("dlna_dmr")
