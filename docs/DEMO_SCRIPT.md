# DroneDog — class demo script (≈12 min)

Run from the USB stick: `DroneDog Demo Kit\DroneDog\DroneDog.exe`. Nothing installs. Cases save to
`DroneDog\Cases\` next to the exe. A finished copy of the whole demo case is already in
`Cases\Demo backup` in case anything goes wrong live.

Before class: plug in, open the exe once, close it. Have `Demo Evidence\` visible in Explorer.

## 1. Open + new case (1 min)
- Start screen: point at **Runs fully offline** and the **green light** top-right (no network).
- New case: name `Class demo`, number `LAB-1`, your name. Leave the folder blank → `Cases\LAB-1`.
- Say: evidence is hashed on intake, never modified; every step is written to a hash-chained custody log.

## 2. Import a disk image (2 min)
- Import → **Add disk image** → `Demo Evidence\1 - SD card image (constructed)\...CONSTRUCTED.dd`.
- Watch the stages: hashing → extracting → detecting → parsing (~20 s).
- Six `FLY*.DAT` appear. One row shows **DELETED**: `_LY140.DAT`, recovered from a deleted FAT entry.
- Open **Artifacts**, expand `_LY140.DAT`: SHA-256 `dce72251…`, inner path `MISC/LOG`, byte offset,
  overwrite check "none detected". Compare the hash with `README_DEMO.txt` beside the image: identical.
- Say: this image was built from real Phantom 4 Pro logs (public research data), then one file was
  deleted on purpose. The recovered bytes match the original exactly.

## 3. Fly it (3 min)
- Flights → click `_LY140.DAT`. Map track coloured by altitude, home/takeoff/landing markers.
- Press **Space**: playback with the aircraft arrow and synced charts (altitude, speed, battery, RC sticks, GPS).
- Switch **Color by → Speed**. Open **Events** on the right; click one to seek.
- Details tab: model line says *Unidentified DJI (V3-era DAT, product code 35)*.
  Say: DroneDog only states what the file proves; the dataset says these are Phantom 4 Pros, the file does not.
- Switch **Imperial** top-right if the room prefers feet.

## 4. Aircraft + phone of the same flight (3 min)
- Import → **Add folder** → `Demo Evidence\4 - Phantom 3 aircraft + phone logs (DROP)\gh-drop` (2 s).
- Flights now show aircraft DATs **and** app TXT logs. Open `FLY002.DAT` → chip **Same flight as #N · App TXT**.
- Tick the **Linked records** overlay: the phone's track (dashed) sits on the aircraft's track.
- Source tab → reasons: serial match, home points X m apart, N frame pairs within Y m.
  Say: the phone log alone was 90 % undecodable; the aircraft's own log recovers the full flight and
  the tool proves they are the same flight.

## 5. Online map, deliberately (1 min)
- Top bar → **Online maps: On**. The light turns **red**; click it: hosts, bytes, "only map tiles are requested".
- Basemap → **Satellite (USGS)**: West Haven, CT under the track. Then **Street (OpenStreetMap)**.
- Turn it **Off**; the light is green again. Say: the toggle is written to the custody log.

## 6. A real device image + a real iPhone backup (1 min, optional)
- Import `2 - NIST E01 (DJI Agras)\...E01` and `3 - iPhone backup (Mavic Pro)\...zip` together.
- E01: 49 DATs found; Agras logs decode events and timing but no track → rows say **NO TRACK**, honestly.
- iPhone backup: DJI GO logs found via the backup's Manifest.db, plus the DJI GO "tips" files linked to
  their flights by serial `08RDD9100100KG`.

## 7. Custody + report (1 min)
- Custody → **Verify chain** → green, N entries, head hash.
- Report → select 2–3 flights → KML + GeoJSON + JSON + HTML → **Export** → Show in folder.
  `manifest.sha256` verifies with `sha256sum -c`. Open `report.html`: evidence hashes, artifacts, links, limitations.

## Importing the class files
New case → Import → Add files/folder. What to expect by aircraft:

| Aircraft | Aircraft DAT | Phone/app TXT |
|---|---|---|
| Phantom 3/4, Inspire 1/2, Mavic Pro, Spark, Matrice 100/200/600 | full decode | full (DJI GO 4, v≤11) |
| Mavic Air, Mavic 2, P4P V2 | aircraft DAT often **encrypted** → recognised, header + attribution only | full (v11/12) |
| Mavic Air 2, Air 2S, Mini 2/3/4, Mavic 3, Avata, newer | encrypted → recognised only | DJI Fly v13+: **header only** (model, serials, start time); no DJI key |
| Enterprise (M300/M350, M30, Mavic 3E) | Pilot 2 DATs decode (checked on M350) | v14 header only |

Nothing is ever skipped silently: every file gets a row with status parsed / encrypted / unsupported / failed.

## If something breaks
- Exe won't start → `DroneDog\DroneDogData\logs\dronedog.log` names the error; fall back to the laptop's
  source checkout: `.venv\Scripts\python.exe -m dronedog.ui`.
- Import stalls → Start → Open case folder → `Cases\Demo backup` (prebuilt, all screens tested).
- No Wi-Fi → stay offline; Settings → Map packs → import `Map packs (offline)\WestHaven_CT_USGS.mbtiles`.
- Someone asks "is it court-ready?" → validation: DAT decoder matches DatCon on 51,000 rows across 6
  flights at rounding level; TXT parser is the MIT dji-log-parser library; both documented in `docs/` and
  `corpus/baselines/`. It is a student build, not a validated commercial tool.

## Likely questions
- **Why "Unidentified DJI"?** The DAT only carries a product code; naming a model from outside knowledge
  would be an unproven claim in an exhibit. The dataset metadata is shown separately as a hint.
- **Can it decrypt the new drones?** No, and it does not try: DMCA 1201 and the handoff's boundary.
  It identifies them, reads what is plaintext, and links them to the phone.
- **Deleted file recovery?** FAT directory entry + cluster chain; it checks whether clusters were reused
  and labels the result (none detected / partial / full overwrite).
- **What leaves the machine?** Nothing unless Online maps is on, and then only tile requests (the viewed
  area). The network panel lists every host and byte count; the choke point is one Python function.
