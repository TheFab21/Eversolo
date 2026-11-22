"""Tests for playing a stream through the Eversolo's DLNA renderer."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

from aiohttp import ClientSession, web
import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "custom_components" / "eversolo"

SOAP = "{http://schemas.xmlsoap.org/soap/envelope/}"
AVT = "{urn:schemas-upnp-org:service:AVTransport:1}"
DIDL = "{urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/}"
DC = "{http://purl.org/dc/elements/1.1/}"
UPNP = "{urn:schemas-upnp-org:metadata-1-0/upnp/}"

FIP_AAC = "http://icecast.radiofrance.fr/fip-hifi.aac"
FIP_LOGO = "https://example.org/fip.png"
CONTROL_PATH = "/AVTransport/8FEDF47F-6B31-11F1-A7C6-800A805F537A/control.xml"


def _load_dlna_module():
    """Load the DLNA module alone: it does not depend on Home Assistant."""
    name = "_eversolo_dlna_tests"
    spec = spec_from_file_location(name, PACKAGE / "dlna.py")
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dlna = _load_dlna_module()


def _description(services: str, url_base: str = "") -> str:
    """Return a device description listing the given services."""
    return (
        '<?xml version="1.0"?>'
        '<root xmlns="urn:schemas-upnp-org:device-1-0">'
        f"{url_base}"
        "<device><deviceType>urn:schemas-upnp-org:device:MediaRenderer:1"
        f"</deviceType><serviceList>{services}</serviceList></device></root>"
    )


def _service(service_type: str, control_url: str) -> str:
    """Return one serviceList entry."""
    return (
        f"<service><serviceType>{service_type}</serviceType>"
        f"<controlURL>{control_url}</controlURL></service>"
    )


RENDERER_DESCRIPTION = _description(
    _service("urn:schemas-upnp-org:service:RenderingControl:1", "/RC/control.xml")
    + _service(dlna.AV_TRANSPORT_SERVICE, CONTROL_PATH)
)


def _action(body: str) -> ET.Element:
    """Return the action element of a SOAP envelope."""
    envelope = ET.fromstring(body)
    assert envelope.tag == f"{SOAP}Envelope"
    action = envelope.find(f"{SOAP}Body")[0]
    assert action.findtext("InstanceID") == "0"
    return action


def _didl_item(set_uri_body: str) -> ET.Element:
    """Return the DIDL item a SetAVTransportURI request announces."""
    metadata = _action(set_uri_body).findtext("CurrentURIMetaData")
    return ET.fromstring(metadata).find(f"{DIDL}item")


def test_set_uri_announces_a_radio_station_with_an_open_mime_type() -> None:
    """The request matches the one verified on a DMP-A6 with an AAC stream."""
    body = dlna.build_set_av_transport_uri(FIP_AAC, "FIP", FIP_LOGO)

    action = _action(body)
    assert action.tag == f"{AVT}SetAVTransportURI"
    assert action.findtext("CurrentURI") == FIP_AAC

    item = _didl_item(body)
    assert item.findtext(f"{DC}title") == "FIP"
    assert item.findtext(f"{UPNP}class") == "object.item.audioItem.audioBroadcast"
    assert item.findtext(f"{UPNP}albumArtURI") == FIP_LOGO
    resource = item.find(f"{DIDL}res")
    assert resource.get("protocolInfo") == "http-get:*:*:*"
    assert resource.text == FIP_AAC


def test_set_uri_survives_markup_in_every_field() -> None:
    """Titles and URLs carrying XML-special characters reach the device intact."""
    url = "http://example.org/stream?a=1&b=<2>"
    title = 'Rock & <Roll> "Live"'
    logo = "http://example.org/logo.png?size=1&fmt=png"
    body = dlna.build_set_av_transport_uri(url, title, logo)

    assert _action(body).findtext("CurrentURI") == url
    item = _didl_item(body)
    assert item.findtext(f"{DC}title") == title
    assert item.findtext(f"{UPNP}albumArtURI") == logo
    assert item.find(f"{DIDL}res").text == url


def test_set_uri_without_logo_omits_the_artwork() -> None:
    """No artwork element is sent when the call names no logo."""
    item = _didl_item(dlna.build_set_av_transport_uri(FIP_AAC, "FIP"))
    assert item.find(f"{UPNP}albumArtURI") is None


def test_play_starts_at_normal_speed() -> None:
    """Play is a separate action: loading a stream does not start it."""
    action = _action(dlna.build_play())
    assert action.tag == f"{AVT}Play"
    assert action.findtext("Speed") == "1"


@pytest.mark.parametrize(
    ("description", "location", "expected"),
    [
        (
            RENDERER_DESCRIPTION,
            "http://172.22.22.128:1639/description.xml",
            f"http://172.22.22.128:1639{CONTROL_PATH}",
        ),
        (
            _description(
                _service(dlna.AV_TRANSPORT_SERVICE, "control.xml"),
                "<URLBase>http://172.22.22.128:1429/AVTransport/</URLBase>",
            ),
            "http://172.22.22.128:1429/description.xml",
            "http://172.22.22.128:1429/AVTransport/control.xml",
        ),
        (
            _description(
                _service(
                    "urn:schemas-upnp-org:service:AVTransport:2",
                    "http://172.22.22.128:1249/avt",
                )
            ),
            "http://172.22.22.128:9999/description.xml",
            "http://172.22.22.128:1249/avt",
        ),
    ],
    ids=["relative-to-location", "relative-to-urlbase", "absolute"],
)
def test_control_url_follows_the_description(
    description: str, location: str, expected: str
) -> None:
    """The control URL comes from the description, never from a stored port."""
    assert dlna.av_transport_control_url(description, location) == expected


@pytest.mark.parametrize(
    "description",
    [
        _description(_service("urn:schemas-upnp-org:service:RenderingControl:1", "/x")),
        _description(_service(dlna.AV_TRANSPORT_SERVICE, "  ")),
        "<html>not a description",
    ],
    ids=["no-avtransport", "empty-control-url", "not-xml"],
)
def test_control_url_absent_when_the_description_offers_none(description: str) -> None:
    """A description without a usable AVTransport service yields nothing."""
    assert dlna.av_transport_control_url(description, "http://h/d.xml") is None


class FakeRenderer:
    """A local HTTP server answering like the Eversolo's DLNA renderer.

    Tests using it need ``socket_enabled``: the Home Assistant test plugin
    blocks every socket otherwise, local ones included.
    """

    def __init__(self, description: str = RENDERER_DESCRIPTION) -> None:
        """Record every action the renderer receives."""
        self.description = description
        self.actions: list[tuple[str, str]] = []
        self.status: dict[str, list[int]] = {"SetAVTransportURI": [], "Play": []}
        self.runner: web.AppRunner | None = None
        self.location = ""

    async def _description(self, _request: web.Request) -> web.Response:
        return web.Response(text=self.description, content_type="text/xml")

    async def _control(self, request: web.Request) -> web.Response:
        soap_action = request.headers["SOAPACTION"]
        assert soap_action.startswith(f'"{dlna.AV_TRANSPORT_SERVICE}#')
        action = soap_action.split("#", 1)[1].rstrip('"')
        self.actions.append((action, await request.text()))
        queued = self.status[action]
        status = queued.pop(0) if len(queued) > 1 else (queued[0] if queued else 200)
        return web.Response(status=status, text="<ok/>")

    async def __aenter__(self) -> FakeRenderer:
        """Serve the description and the control endpoint on a free port."""
        app = web.Application()
        app.router.add_get("/description.xml", self._description)
        app.router.add_post(CONTROL_PATH, self._control)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = self.runner.addresses[0][1]
        self.location = f"http://127.0.0.1:{port}/description.xml"
        return self

    async def __aexit__(self, *_exc: object) -> None:
        """Stop serving."""
        assert self.runner
        await self.runner.cleanup()


@pytest.mark.usefixtures("socket_enabled")
async def test_play_stream_loads_then_plays() -> None:
    """The stream is loaded as a radio station, then started."""
    async with FakeRenderer() as renderer, ClientSession() as session:
        await dlna.async_play_stream(
            session, renderer.location, FIP_AAC, "FIP", FIP_LOGO
        )

    assert [action for action, _ in renderer.actions] == [
        "SetAVTransportURI",
        "Play",
    ]
    item = _didl_item(renderer.actions[0][1])
    assert item.findtext(f"{UPNP}class") == "object.item.audioItem.audioBroadcast"
    assert item.find(f"{DIDL}res").text == FIP_AAC


@pytest.mark.usefixtures("socket_enabled")
async def test_refused_stream_is_never_started() -> None:
    """A renderer refusing the stream gets no Play, and the caller hears why."""
    async with FakeRenderer() as renderer, ClientSession() as session:
        renderer.status["SetAVTransportURI"] = [500]
        with pytest.raises(dlna.EversoloDlnaError, match="HTTP 500"):
            await dlna.async_play_stream(session, renderer.location, FIP_AAC, "FIP")

    assert [action for action, _ in renderer.actions] == ["SetAVTransportURI"]


@pytest.fixture
def no_play_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry Play without waiting."""
    monkeypatch.setattr(dlna, "PLAY_RETRY_DELAY", 0)


@pytest.mark.usefixtures("socket_enabled", "no_play_delay")
async def test_play_is_asked_again_while_the_stream_loads() -> None:
    """A renderer refusing Play while it loads gets asked again until it plays."""
    async with FakeRenderer() as renderer, ClientSession() as session:
        renderer.status["Play"] = [500, 500, 200]
        await dlna.async_play_stream(session, renderer.location, FIP_AAC, "FIP")

    assert [action for action, _ in renderer.actions] == [
        "SetAVTransportURI",
        "Play",
        "Play",
        "Play",
    ]


@pytest.mark.usefixtures("socket_enabled", "no_play_delay")
async def test_play_refused_for_good_is_an_error() -> None:
    """Asking again has a limit, and the last refusal is reported."""
    async with FakeRenderer() as renderer, ClientSession() as session:
        renderer.status["Play"] = [500]
        with pytest.raises(dlna.EversoloDlnaError, match="Play answered HTTP 500"):
            await dlna.async_play_stream(session, renderer.location, FIP_AAC, "FIP")

    plays = [action for action, _ in renderer.actions if action == "Play"]
    assert len(plays) == dlna.PLAY_ATTEMPTS


@pytest.mark.usefixtures("socket_enabled")
async def test_description_without_avtransport_is_an_error() -> None:
    """A device that is not a renderer is reported, not silently skipped."""
    no_transport = _description(
        _service("urn:schemas-upnp-org:service:RenderingControl:1", "/rc")
    )
    async with (
        FakeRenderer(no_transport) as renderer,
        ClientSession() as session,
    ):
        with pytest.raises(dlna.EversoloDlnaError, match="no AVTransport"):
            await dlna.async_play_stream(session, renderer.location, FIP_AAC, "FIP")
    assert renderer.actions == []


@pytest.mark.usefixtures("socket_enabled")
async def test_stale_location_is_an_error() -> None:
    """A port that no longer answers raises, so the caller can try another."""
    async with FakeRenderer() as renderer:
        stale = renderer.location
    async with ClientSession() as session:
        with pytest.raises(dlna.EversoloDlnaError, match="unreachable"):
            await dlna.async_play_stream(session, stale, FIP_AAC, "FIP")
