# Changelog

## 1.0.5b3

- `scripts/probe_eversolo_api.py` can ask write-shaped names without parameters, behind `--probe-write-names`, to find whether a handler that plays a URL exists at all. Two names known to exist ride along as positive controls, and nothing that could act without parameters is asked.

## 1.0.5b2

- `scripts/probe_eversolo_api.py` now reports the handlers that answer device status 805, which means the name exists and wants parameters, where an unknown name answers 405. That distinction is what tells a real endpoint from a guess.
- Keep every play-queue entry in the probe's report. The queue was read through the same sampler that trims a list to three items to keep the report readable, so a device reporting eight stations was written down as three, and that list is the point of the run.
- Probe a nonsense service namespace as a control, so an answer of 801 ("the application element is not registered") on the radio namespaces can be read against what an unknown namespace says.
- Added read-shaped candidate names for the services the device names itself: the internet-radio app it calls `rb`, whose station ids are radio-browser UUIDs, the AirAble family behind `addAirAbleMusicListToPlayQueue`, and TuneIn. Alternate service paths are probed too.

## 1.0.5b1

- Decide the power button from whether the device answers its API: a device that answers is powered down, one that stopped answering is woken with Wake-on-LAN, so a reported standby no longer sends a magic packet to a device that is still up.
- Turn the player off as soon as the device stops answering a requested shutdown, instead of holding its previous state for a few polls.

## 1.0.4b1

- Name what is actually playing during a DLNA stream: the firmware leaves `playingMusic` on the station that stopped, so the live audio block is read first, and the stopped station's artwork is no longer shown.
- Keep a station reachable when the internet-radio queue holds it twice: the first keeps its plain name and the repeat is qualified with its bitrate.
- Read playlists wrapped in an `obj` member, which this firmware uses, so they appear in the source list and the media browser instead of being skipped.

## 1.0.3b1

- Expose the play queue as selectable sources: on the internet-radio source the firmware lists the stations there, so `media_player.select_source` can now start one by name.
- Report a clear error when `media_player.play_media` receives something other than a media-browser identifier, such as a stream URL, instead of raising a JSON decoding error.
- `scripts/probe_eversolo_api.py` now prints the whole play queue with each entry's title and stream URL.

## 1.0.2b2

- Read an application's name from the `title` key the streaming list uses, so TuneIn Radio no longer appears as "Player" on DMP-A6 firmware v1.6.15.

## 1.0.2b1

- Let `media_player.select_source` select device favorites, local playlists, and installed music apps, not only hardware inputs.
- Match requested source names ignoring case and surrounding spaces, and list the available sources when one is unknown.
- Request library endpoints under both `/ZidooMusicControl/v2` and `/MusicControl/v2`, remembering the prefix the firmware answers on.
- Read a missing endpoint (HTTP 404/405/501) as unsupported instead of as an unreachable device.
- Report the active input from the device-provided index instead of its position in the list.
- Keep the last known state through a few missed polls instead of logging an error for every timeout.
- Requested `wakeonlan>=3.3.0` instead of pinning 4.0.0: Home Assistant depends on wakeonlan itself, so hassfest rejected both the conflicting pin and any pin at all.
- Added `scripts/probe_eversolo_api.py` to report which API endpoints a firmware serves, and listed inputs, favorites, playlists, and apps in diagnostics.

## 1.0.1b1

- Keep generating `eversolo.zip` as a GitHub release asset.
- Let HACS install release source files instead of the ZIP asset.
- Preserve prerelease support without falling back to commit-only ZIP downloads.

## 1.0.0b1

- Updated development and runtime dependencies for Home Assistant 2026.7 and Python 3.14.
- Added a bounded asynchronous API client with device-status error mapping.
- Reduced polling load by separating real-time playback from slower settings.
- Added dynamic model capabilities and preserved device-provided I/O indexes and tags.
- Fixed unmute, source refresh, UTC media position timestamps, and Wake-on-LAN ports.
- Moved runtime state to ConfigEntry.runtime_data.
- Added unique device configuration, reconfiguration, mDNS discovery, and standby options.
- Added repeat, shuffle, rich audio metadata, artwork proxying, queue browsing, favorites, playlists, and library search.
- Added dynamic streaming-app discovery, app icons, remote launch, and the Eversolo cross-service favorites launcher.
- Added diagnostics, English/French translations, entity icons, and local light/dark brand assets.
- Disabled redundant legacy command buttons by default while preserving unique IDs.
- Added Ruff formatting, grouped Dependabot updates, and an initial API test suite.
