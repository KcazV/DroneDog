# What DroneDog cannot decode yet, and what each gap would take

Status: research note for the instructor-facing design defence, 2026-10-22. No code changes.
It covers the artifacts DroneDog only *recognises* today, and for each one gives the
technical, legal and validation work needed to go further.

**Conventions.** **[V]** = verified fact, with the source inline (a URL, or a repo file
we read). **[I]** = our inference or recommendation, not established by a source.
Effort: **S** = under 1 week, **M** = 1-4 weeks, **L** = over a month *or* blocked on an
outside party (a key, a licence, samples).

**Boundary (HANDOFF.md section 2, DECISIONS.md D7).** DroneDog does not break ciphers,
extract keys or exploit devices. It decrypts only through a lawful path someone supplies:
plaintext, an official tool, a published decryptor that is licensed for our use, or DJI's
keychain service with a valid key. Every section below stays inside that boundary. Where
the only route would be device access (chip-off, root), that work belongs to the
operator's separate workstream, and DroneDog only reads the image that comes out of it.

---

## 0. Summary

| # | Capability | DroneDog today | Main blocker | Effort | Ground truth we already hold |
|---|---|---|---|---|---|
| 1 | App TXT v13/v14 flight records | Plaintext header, serials and totals read. Records reported as encrypted. The core already decrypts **if** a keychain is supplied. | DJI developer key + agency approval to contact DJI | **M** plumbing; **L** if no key | v14 logs paired with official-decoder JSON or vendor CSVs (section 1.5) |
| 2a | Encrypted aircraft DAT (`LOGH`): attribution from the header | Recognised; reported as "encrypted, attribution only" | One real `LOGH` sample (the corpus has none) | **S** once a sample exists | none |
| 2b | Encrypted aircraft DAT: decrypting the content | Not built (D9) | Licence or permission from D3VL / Fodogu, or an operator-supplied decrypted file | **L** | none |
| 3 | Controller/phone internal storage, newer aircraft eMMC | Logical dumps, iOS backups and FAT/exFAT images work | Physical access sits outside the repo; file-based encryption (FBE) on phones | **M** (ext4/Android paths) | CFReDS DF020 Android, DF068 iOS |
| 4a | Autel / Parrot / Skydio | None | Little public format work | **M** (Parrot) to **L** | none |
| 4b | V1 Agras MG-1S / A3 DATs: position track | Events, timing and serials only; 0 frames | No published record layout | **M-L** | CFReDS DF040/DF041 |

The quickest wins are 2a and the keychain plumbing for 1. Neither one touches a cipher.

---

## 1. DJI app flight records `DJIFlightRecord_*.txt`, log versions 13 and later

### 1.1 How the encryption works
- **[V]** From version 13 on, each record is XOR-layered and then AES-256-CBC encrypted
  with PKCS7 padding. In dji-log-parser 0.5.7, `src/decoder.rs` uses `cbc::Decryptor<Aes256>`.
  Keys are issued **per feature point**: `src/keychain/feature_point.rs` lists 15 of them
  (BaseFeature, VisionFeature, WaypointFeature, AgricultureFeature, AirLinkFeature,
  AfterSalesFeature, DJIFlyCustomFeature, PlaintextFeature, FlightHubFeature,
  GimbalFeature, RCFeature, CameraFeature, BatteryFeature, FlySafeFeature,
  SecurityFeature). The record-type-to-feature mapping differs between v13 and v14.
- **[V]** The log stores its own keys only in wrapped (encrypted) form, in `KeyStorage`
  records. A `KeyStorageRecover` record starts a new keychain, so one file can hold
  several keychains (`src/lib.rs`, `keychains_request_with_custom_params`).
- **[V]** To unwrap the keys, the parser POSTs to
  `https://dev.dji.com/openapi/v1/flight-records/keychains`. The request carries an
  `Api-Key` header and the JSON body `{version, department, keychainsArray:[[{featurePoint,
  aesCiphertext}]]}`. The reply is `{featurePoint, aesKey, aesIv}` for each entry
  (`src/keychain/api.rs`). The body holds only the wrapped key blobs plus the log
  version and the "department" (the app family: DJI Fly, Pilot, Glasses...). It does
  not hold track points or header fields.
  Source: [lvauvillier/dji-log-parser](https://github.com/lvauvillier/dji-log-parser) (MIT).
- **[V]** DJI's own [FlightRecordParsingLib](https://github.com/dji-sdk/FlightRecordParsingLib)
  (MIT portions, C++) also needs an App Key. You get one by creating an "Open API" app
  on developer.dji.com, and the library uses curl/openssl to fetch keys from DJI's
  service. Its README documents version 13 only.
- **[V]** After keychains are fetched, they can be "stored along with the original log
  for further offline use" ([docs.rs/dji-log-parser](https://docs.rs/dji-log-parser)).

### 1.2 What stays readable without a key
- **[V]** In v13/v14 the details block sits in an "Auxiliary Info" section that is only
  XOR-obfuscated, not AES-encrypted (`src/layout/auxiliary.rs`: `XorDecoder::new(reader, 0)`).
  It holds: start time; take-off latitude/longitude; street, city and area strings; total
  distance and time; maximum height and speeds; photo count and video time; take-off
  altitude; product type; aircraft name; **aircraft, camera, RC and battery serial
  numbers**; app platform and version (`src/layout/details.rs`). A second auxiliary block
  gives the keychain version and department.
- **[V]** DroneDog reads all of this today and reports the records as encrypted
  (`core/src/txt.rs`; warning "log version N records are AES encrypted; no DJI keychains
  were supplied...").

### 1.3 What DroneDog already has, and what is missing
- **[V] Built:** `parse_txt(path, keychains_json)` decrypts v13+ entirely offline when it
  is handed a cached keychain JSON. `core/tests/offline_guard.rs` scans the source to
  prove the core never calls `fetch_keychains` / `KeychainsRequest::fetch`.
  `AdapterContext.keychains_json` is the slot for it (`dronedog/adapters/base.py`).
- **[V] Missing:** nothing fills that slot yet. There is no CLI/UI import, no request
  export, and no methodology-log entry.
- **[I] What D5 needs (no crypto involved):**
  1. **Export** a request file. `DJILog::keychains_request()` builds the request without
     any network access, and `KeychainsRequest` is `Serialize`. Write it to
     `<case>/keychains/<log-sha256>.request.json`, hash it, and log it.
  2. **Fetch** on a separate connected machine, outside DroneDog. Any HTTP client sending
     the agency's key will do. Hash the response.
  3. **Import** `<log-sha256>.keychains.json` beside the evidence. Record in the custody
     log: request hash, response hash, key fingerprint (never the key itself), fetch time
     and operator. Then re-parse. A re-run decrypts offline from the cached file, which
     meets the HANDOFF section 10 reproducibility property.

### 1.4 Legal and process considerations
- **[V]** DJI's API License Agreement (last modified 2024-01-25) allows use "solely for you
  to develop and provide Your Service to End Users". Any other use needs "DJI's prior
  written consent". Keys may not be shared and can be revoked at any time. Governing law
  is Hong Kong SAR, with arbitration administered by HKIAC. The agreement says nothing
  about law-enforcement or forensic use.
  ([developer.dji.com/policies/flight_record](https://developer.dji.com/policies/flight_record))
- **[I]** Each request tells DJI that this agency's key is unlocking these specific
  wrapped keys at this time. Because the keys are generated per log, DJI could plausibly
  link the request to the aircraft or account that made the log. This is an
  operational-security disclosure to the manufacturer, and it should be approved under
  agency policy (supervisor or prosecutor sign-off) before the first fetch.
- **[I]** Counsel should confirm that the existing warrant or consent covers decrypting the
  seized file through a third party. Counsel should also say whether the agency needs
  DJI's written consent to use the API for casework, or whether it should go through a
  DJI-partnered vendor. That vendor route is a forum claim only
  ([phantompilots thread](https://phantompilots.com/threads/decrypt-any-dji-flight-log-with-flight-reader.151529/)).
- **[I] Rejected alternative:** uploading the whole log to a web viewer such as Airdata or
  dronedesk, which fetch keychains server-side
  ([logviewer.dronedesk.io/how-it-works](https://logviewer.dronedesk.io/how-it-works)).
  It sends the complete evidence file to a fourth party and breaks D4.

### 1.5 Validation plan
- **[V]** The corpus already holds v14 logs paired with independently decoded output:
  - Mini 2 and Air 2S logs with **FlightRecordParsingLib JSON** (`corpus/raw/gh-therobbrennan-dji-flight-record-review`);
  - 7 DJI FPV logs with decrypted CSVs (`gh-dronenlp-dataset-casestudy`, MIT);
  - Matrice 350 RTK logs with Airdata CSVs (`gh-isensuiuc-dji-flight-log`);
  - a Mini 4 Pro log with CSV (`gh-ammar321an-decryption-dji-log`).
- **[I]** Once a key exists: fetch keychains for those files once, freeze them as fixtures,
  and diff DroneDog frames field by field against the official-decoder JSON. Report error
  bounds the same way `corpus/baselines/dat/VALIDATION.md` does for DatCon. With no key,
  the request-export and import code can still be tested end to end against a synthetic
  keychain file to confirm the file formats are right.

**Effort: M** for plumbing, UI and tests. **L / blocked** until the agency holds a DJI key and the approvals above.

---

## 2. Encrypted aircraft DATs (Mavic Air 2, Mini 2/3/4, Air 2S, Mavic 3, Avata, FPV, M30/M300...)

### 2.1 What published research says about the encryption
- **[V]** Lan & Lee, HTX Singapore, *Drone Forensics: A Case Study on DJI Mavic Air 2*,
  ICACT 2021, [doi:10.23919/ICACT51234.2021.9370578](https://doi.org/10.23919/ICACT51234.2021.9370578)
  ([PDF](https://icact.org/upload/2021/0119/20210119_finalpaper.pdf)). Mavic Air 2 firmware V01.00.0250:
  - DATs downloaded through FTP or DJI Assistant 2 are encrypted with **AES**, using a key
    derived from a per-file 16-byte random seed. A Trusted Application in the
    **TrustZone** generates both the seed and the key.
  - The paper says "the FTP service of the newer DJI drone models, namely the Mavic Mini
    and Mavic Air 2" works this way.
  - The encrypted file starts with a 160-byte header: `LOGH`, the model code (`WM231`),
    the **board serial number** ("different from the drone serial number") and the random
    seed.
  - On that firmware, the DATs the authors read by chip-off from the 16 GB eMMC were plain
    `DJI LOG V3` and decoded with DatCon. Encryption was applied *on export*, not at rest.
  - The paper also states: "currently there is no public avenue available to decrypt the
    flight data of the newer DJI drone models."
  - The paper does not state the cipher mode. Do not write "AES-CBC" for DATs without a
    source.
- **[V]** Taylor, *A Digital Forensics Case Study of the DJI Mini 3 Pro and DJI RC*,
  [arXiv:2309.10487](https://arxiv.org/abs/2309.10487) (2023). A Mini 3 Pro DAT pulled with
  Assistant 2 gave 0-1 KB of DatCon output, but a model number and a serial-like string
  were readable in plain text at the start of the file. The same paper cites Stanković,
  Mirza & Karabiyik, *UAV Forensics: DJI Mini 2 Case Study*,
  [Drones 5(2):49 (2021)](https://doi.org/10.3390/drones5020049), who could not decode
  Mini 2 DATs with DatCon.
- **[V]** DroneDog's own corpus has **zero** `LOGH` files. The 2023 `MCDatFlightRecords`
  DATs from a Matrice 350 RTK (publisher's description) are *plaintext* V3 (product code
  89) and decode fully (`corpus/baselines/dat/corpus_scan.md`). The model name does not
  tell you whether a DAT is encrypted (DAT_FORMAT.md section 7).
- **[I]** Mavic 2 aircraft-side DATs are **unmeasured**. The Mavic 2 Zoom app-side DAT in
  the CFReDS DF068 iOS backup decodes (code 41). CFReDS DF067/DF069/DF081/DF082 hold
  Mavic 2 images, but the walker has not scanned them for aircraft DATs yet. That is an
  **S** task, and it settles the Mavic 2 row of the coverage map with evidence.

### 2.2 Published decryptors and analysis tools

| Tool / paper | Models | Licence (checked 2026-10-22 via GitHub API) | Last push | Usable by DroneDog? |
|---|---|---|---|---|
| [D3VL/DJI-DAT-Decryptor](https://github.com/D3VL/DJI-DAT-Decryptor) | Goggles 2, O3 Air Unit, Avata (Assistant 2 exports) | MIT + Commons Clause v1.0, D3VL LTD 2023: no "Sell" where value derives "entirely or substantially" from it | 2023-03-17 | **No** to bundling or porting (D9). Needs a written commercial licence. |
| [FOSEGU/Fodogu](https://github.com/FOSEGU/Fodogu) | Mini 2 FTP DAT "E2" | **none** (all rights reserved) | 2024-04-03 | **No** without written permission. The README links a [DBpia paper](https://www.dbpia.co.kr/journal/articleDetail?nodeId=NODE11343534) (page timed out; title not confirmed). |
| DROP, Clark, Meffert, Baggili, Breitinger, *Digital Investigation* 22 (2017), [doi:10.1016/j.diin.2017.06.013](https://doi.org/10.1016/j.diin.2017.06.013) | Phantom 3 | MIT | - | XOR obfuscation only, not encryption. **Already covered** (V1 decoder). |
| Kao et al., *Drone Forensic Investigation: DJI Spark Drone as A Case Study*, Procedia CS 159:1890-1899 (2019), [doi:10.1016/j.procs.2019.09.361](https://doi.org/10.1016/j.procs.2019.09.361); Yang, Chuang, Kao, Procedia CS 192 (2021), [doi:10.1016/j.procs.2021.08.139](https://doi.org/10.1016/j.procs.2021.08.139) | Spark, Mavic Air | paper | - | Plaintext V3 era. **Already covered.** Their timing work between data sources informs correlation. |
| DRDP, Zhao, Wang, Liao, *Drones* 8(7):281 (2024), [doi:10.3390/drones8070281](https://doi.org/10.3390/drones8070281) | Mavic 2 Pro study | **none** | 2025-04-12 | Corroboration only (DAT_FORMAT.md section 0) |
| DJI Decrypt Tool (official), v1.0.0.0, Windows, release notes 2021-10-13 ([M30 downloads](https://enterprise.dji.com/matrice-30/downloads)) | M30, M350, M400 enterprise SD cards | DJI EULA | - | Official path. **[I]** It decrypts the encrypted SD card (media), not flight-controller DATs. Wrapping it is allowed under HANDOFF section 3, but only on a Windows host that has the tool installed. |

Bouafif et al. ([NTMS 2018, doi:10.1109/NTMS.2018.8328747](https://doi.org/10.1109/NTMS.2018.8328747))
covers a Parrot AR Drone 2.0, not DJI (see section 4).

### 2.3 What DroneDog would need

**2a. Header attribution, no decryption. Effort S once a sample exists.**
- **[V]** DAT_FORMAT.md section 7 currently says that after the `LOGH` magic and body
  offset "everything else is ciphertext". Lan & Lee contradicts this for Mavic Air 2.
- **[I]** Parse the plaintext model code and board serial and report them as attribution
  evidence ("board serial, not the aircraft SN"). This is reading cleartext, not attacking
  a cipher. **Blocked** until we have one real `LOGH` file to verify the offsets on.
  Candidates: a lab-owned Mini 2/3 or Air 2S pulled with Assistant 2, or published case
  data.

**2b. Content decryption. Effort L.**
- **Legal [I]:** one of the following:
  - a written commercial licence from D3VL LTD or permission from the Fodogu authors,
    plus counsel review under HANDOFF section 2.4 (DMCA 1201, EAR); or
  - the **operator-supplied decrypted artifact** route (HANDOFF section 5, dark-platform
    slot). The examiner runs the third-party tool outside DroneDog, and DroneDog ingests
    the output, recording input hash, output hash, and tool name and version in the
    custody log. This route needs no licence for DroneDog itself and keeps the tool
    unbundled.
- **Key handling [I]:** neither README says where its key material comes from, and
  DroneDog deliberately did not read the decryption code (DAT_FORMAT.md section 0). If
  those tools embed manufacturer keys, shipping them would mean redistributing DJI key
  material. That is the core reason they stay out of tree.
- **Validation [I]:**
  - The decrypted output should be ordinary V3 records, so it can be checked for internal
    consistency: CRC-valid share, recDefs present, and IMU vs GPS track agreement.
  - It can be compared against the **same flight's** v14 TXT once decrypted via the
    official keychain (section 1), and against a surveyed ground-truth flight.
  - Each tool would be validated per model and firmware, because the Mini 2 lesson in 2.1
    shows formats change with firmware updates.

---

## 3. Newer storage: aircraft eMMC, screen controllers, phone apps

| Source | Logical extraction reaches | Physical extraction reaches | DroneDog today |
|---|---|---|---|
| Aircraft internal eMMC (Mavic Air 2 class) | **[V]** FTP / Assistant 2 only, and files come out AES-encrypted (Lan & Lee) | **[V]** chip-off / ISP gave plaintext V3 DATs plus `Blackbox`, `System`, `Upgrade`, `Logs` folders on fw V01.00.0250. **[I]** The filesystem type isn't stated (ext4 is typical for DJI's Android/Linux-based firmware). Newer firmware may encrypt at rest. | Would ingest the resulting image: pytsk3 enumerates ext2/3/4 **[I: untested on drone eMMC; D2 prioritised FAT/exFAT]**. The acquisition itself is outside the repo. |
| DJI RC (screen controller) | **[V]** Over USB (MTP, no root): `DJIFlightRecord_*.txt` in the internal-storage root and in `Android/data/dji.go.v5/files/FlightRecord`; a controller-side `.DAT` that DatCon decoded, with the flight-controller serial in plain text (Taylor 2023) | **[V]** Needs root, an exploit or chip work. Taylor cites root methods by others that were not disclosed. | TXT + plaintext DAT handled if the dump is supplied. **[I]** DJI RC Pro, RC 2 and RC Plus assumed similar (a [forum report](https://mavicpilots.com/threads/help-with-airdata-uav-file-uploads-2025.151758/post-1713590) gives the same `dji.go.v5` path on the RC 2). Not verified. |
| Android phone, DJI Fly / GO 4 | **[V]** `Android/data/dji.go.v5/files/FlightRecord` (DJI Fly, same forum source). DJI GO 4 = `dji.go.v4` (CFReDS DF020 sample in corpus). | **[V]** Android 10+ devices must use file-based encryption. Credential-encrypted storage is "only available after the user has unlocked the device" ([AOSP FBE](https://source.android.com/docs/security/features/encryption/file-based)). **[I]** A physical image of a locked phone gives ciphertext app data. Physical extraction does not get around the passcode. | Android logical dumps ingested (`dronedog/ingest/walker.py`) |
| iPhone, DJI Fly / GO | **[V]** iTunes/Finder backup: DroneDog decoded a Mavic 2 Zoom app-side DAT from the DF068 iOS backup. **[V]** Encrypted backups need the backup password, and DroneDog reports this (`dronedog/ingest/ios.py`). | Full file-system extraction is done with commercial tools outside the repo | Backups supported |
| App databases and caches (account, aircraft pairing, map cache) | **[I]** Present in the app sandbox; names and schemas are not mapped by us | same | **Not built.** Needs a research corpus first; we will not invent paths. |

**Effort M** to add Android/RC path rules, ext4 testing and app-database parsers.
**Validation [I]:** build test devices with known flights (a DJI RC and an Android phone).
Take a logical dump and a full file-system dump of each. Then show that DroneDog finds
every TXT/DAT that the app's own flight list shows, and that hashes match across
extraction methods.

---

## 4. Other gaps

### 4a. Non-DJI aircraft
- **Parrot [V]:** ANAFI full FDR `.bin` logs are encrypted, and only Parrot can read them.
  The "FDR-lite" telemetry can be read (a `tlmb2csv.py` conversion exists), and FreeFlight
  exports GUTMA JSON. Sources: Parrot developer forum
  ([FDR on SD](https://forum.developer.parrot.com/t/fdr-files-on-sd-card-anafi/11162),
  [reading .bin](https://forum.developer.parrot.com/t/reading-flight-log-bin-files/26546)).
  Older AR Drone 2.0 / Bebop: Bouafif et al. (NTMS 2018, above).
  **[I] Effort M** for GUTMA JSON and FDR-lite adapters. FDR `.bin` stays out of scope.
- **Autel [V]:** the EVO II manual says flight logs upload to Autel servers in the
  continental US only when "Flight Log Backup" is enabled
  ([manual](https://pdf.nauticexpo.com/pdf/autel-robotics/evo-ii-series-user-manual-en-issue-version/70426-125747.html)).
  We found no published forensic parser. **[I] Effort L**, greenfield. A cloud copy may be
  reachable through US legal process to Autel.
- **Skydio [I]:** we found no public log-format research in this pass. Treat it as
  unknown. **Effort L.**

### 4b. DJI Assistant 2 exports
- **[V]** The zlib container (283-byte entries) is decoded, and every `FLY*.DAT` entry is
  extracted (DAT_FORMAT.md section 2.4). Exports whose inner entry is `LOGH`, or carries a
  newer product tag (e.g. `G-E3T`, Goggles 2), are recognised only; see section 2.
- **[I] Effort S** to also list non-FLY entries (system logs) as artifacts with hashes.

### 4c. V1-format DATs from Agras MG-1S and A3 flight controllers
- **[V]** CFReDS DF040 (48 files): the record framing is CRC-valid, and the boot and event
  text can be read (`Board:"zd900"`, `Mc ID`, `Mc Ver v2.0.0.47`). The files carry no
  recDefs, and none of the known position record types appear (types seen: 199, 1006,
  1033, 1075, 1085, 2286-2311, 52720...). A scan for coordinate pairs found nothing
  verifiable, so DroneDog reports events, timing and serials, with 0 frames
  (DAT_FORMAT.md section 2.3).
- **What it needs [I]:**
  1. A published layout for those record types. The candidates to read as a spec only
     are dji-firmware-tools (GPL, read-only under D9) or a later DatCon release (closed
     source, so baselines only).
  2. Independent ground truth for the same flights: a paired DJI MG / GO app TXT from the
     DF040/DF041 images, or the CFReDS README flight descriptions.
  3. No layout ships without that ground truth (HANDOFF section 2.6). Guessing offsets
     would not stand up in court.
- **Effort M** if a layout source exists, **L** if it has to be derived from
  ground-truth-only correlation.

---

## 5. Recommended order

1. **S:** scan CFReDS DF067-DF082 (Mavic 2 family) for aircraft DATs, so the coverage map
   rests on measurement.
2. **M:** keychain request export and import plus custody logging (D5). The decrypt path
   is already offline and tested.
3. **S, needs a sample:** `LOGH` header attribution (model + board serial).
4. **M:** Android/RC path rules and ext4 image test; Parrot GUTMA/FDR-lite.
5. **L, gated on licence or counsel:** D3VL/Fodogu via the operator-supplied-artifact
   slot; DJI Decrypt Tool wrapper.

The design argument for the instructor: DroneDog already gets the most out of every file
that is lawfully readable. The plaintext headers already give attribution (serials, start
time, take-off point, totals). Each remaining gap comes down to a key, a licence, a sample
or a device-access step outside the evidence tool. None of them is something DroneDog
should solve by attacking the encryption.
