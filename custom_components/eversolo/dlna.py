"""Play a stream through the Eversolo's own DLNA renderer, as a radio station.

The Eversolo's HTTP API has no handler that plays an arbitrary URL, so a
stream has to go through the DLNA renderer the device runs alongside it.
Home Assistant's own ``dlna_dmr`` integration can reach that renderer, but it
announces every stream as a music track with a MIME type guessed from the
URL, and the DMP-A6 refuses AAC announced that way (UPnP error 714). Announced
as a radio station with an unspecified MIME type, the same AAC stream plays.

The renderer's port is not stable: it has been seen to change across
restarts. Nothing here stores it. The caller passes the description URLs Home
Assistant currently knows for the device, and the control URL is read from the
description on every call.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from aiohttp import ClientError, ClientSession, ClientTimeout

AV_TRANSPORT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
MEDIA_RENDERER_DEVICE = "urn:schemas-upnp-org:device:MediaRenderer:1"
RENDERER_SEARCH_TARGETS = (MEDIA_RENDERER_DEVICE, AV_TRANSPORT_SERVICE)

DEVICE_NAMESPACE = "{urn:schemas-upnp-org:device-1-0}"
REQUEST_TIMEOUT = ClientTimeout(total=10)

# A renderer still loading the stream can refuse Play (UPnP 701, transition not
# available). Home Assistant's dlna_dmr waits up to five seconds for the
# renderer to allow Play; asking again for as long gives the same allowance.
PLAY_ATTEMPTS = 10
PLAY_RETRY_DELAY = 0.5


class EversoloDlnaError(Exception):
    """The renderer could not be reached or refused the stream."""


class EversoloDlnaRefusedError(EversoloDlnaError):
    """The renderer answered, but refused the action."""


def _xml(value: str) -> str:
    """Escape text for an XML element or attribute."""
    return escape(value, {'"': "&quot;"})


def build_didl(url: str, title: str, logo: str | None = None) -> str:
    """Describe the stream as a radio station, leaving the MIME type open."""
    art = f"<upnp:albumArtURI>{_xml(logo)}</upnp:albumArtURI>" if logo else ""
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
        ' xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="0" parentID="-1" restricted="1">'
        f"<dc:title>{_xml(title)}</dc:title>"
        "<upnp:class>object.item.audioItem.audioBroadcast</upnp:class>"
        f"{art}"
        f'<res protocolInfo="http-get:*:*:*">{_xml(url)}</res>'
        "</item></DIDL-Lite>"
    )


def _envelope(action: str, arguments: str) -> str:
    """Wrap an AVTransport action in a SOAP envelope."""
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{AV_TRANSPORT_SERVICE}">'
        f"<InstanceID>0</InstanceID>{arguments}"
        f"</u:{action}></s:Body></s:Envelope>"
    )


def build_set_av_transport_uri(url: str, title: str, logo: str | None = None) -> str:
    """Build the request that loads the stream without playing it."""
    return _envelope(
        "SetAVTransportURI",
        f"<CurrentURI>{_xml(url)}</CurrentURI>"
        f"<CurrentURIMetaData>{_xml(build_didl(url, title, logo))}"
        "</CurrentURIMetaData>",
    )


def build_play() -> str:
    """Build the request that starts what was loaded."""
    return _envelope("Play", "<Speed>1</Speed>")


def av_transport_control_url(description: str, location: str) -> str | None:
    """Return the absolute AVTransport control URL a device description gives.

    A relative control URL resolves against the description's URLBase when
    present, otherwise against the URL the description was fetched from.
    """
    try:
        root = ET.fromstring(description)
    except ET.ParseError:
        return None
    base = root.findtext(f"{DEVICE_NAMESPACE}URLBase") or location
    for service in root.iter(f"{DEVICE_NAMESPACE}service"):
        service_type = service.findtext(f"{DEVICE_NAMESPACE}serviceType") or ""
        if not service_type.startswith("urn:schemas-upnp-org:service:AVTransport:"):
            continue
        control = (service.findtext(f"{DEVICE_NAMESPACE}controlURL") or "").strip()
        if control:
            return urljoin(base, control)
    return None


async def _async_soap(
    session: ClientSession, control_url: str, action: str, body: str
) -> None:
    """Send one AVTransport action and require the renderer to accept it."""
    try:
        async with session.post(
            control_url,
            data=body.encode(),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": f'"{AV_TRANSPORT_SERVICE}#{action}"',
            },
            timeout=REQUEST_TIMEOUT,
        ) as response:
            if response.status != 200:
                raise EversoloDlnaRefusedError(
                    f"{action} answered HTTP {response.status}: "
                    f"{(await response.text())[:200]}"
                )
    except (ClientError, TimeoutError) as err:
        raise EversoloDlnaError(f"{action} failed: {err!r}") from err


async def async_play_stream(
    session: ClientSession,
    location: str,
    url: str,
    title: str,
    logo: str | None = None,
) -> None:
    """Load a stream on the renderer described at ``location`` and play it."""
    try:
        async with session.get(location, timeout=REQUEST_TIMEOUT) as response:
            if response.status != 200:
                raise EversoloDlnaError(
                    f"Device description at {location} answered HTTP {response.status}"
                )
            description = await response.text()
    except (ClientError, TimeoutError) as err:
        raise EversoloDlnaError(
            f"Device description at {location} is unreachable: {err!r}"
        ) from err

    control_url = av_transport_control_url(description, location)
    if control_url is None:
        raise EversoloDlnaError(
            f"Device description at {location} offers no AVTransport service"
        )
    await _async_soap(
        session,
        control_url,
        "SetAVTransportURI",
        build_set_av_transport_uri(url, title, logo),
    )
    for attempt in range(1, PLAY_ATTEMPTS + 1):
        try:
            await _async_soap(session, control_url, "Play", build_play())
        except EversoloDlnaRefusedError:
            if attempt == PLAY_ATTEMPTS:
                raise
            await asyncio.sleep(PLAY_RETRY_DELAY)
        else:
            return
