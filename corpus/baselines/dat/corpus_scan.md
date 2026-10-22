# DAT corpus scan

Generated 2026-10-21 22:40 UTC by `tools/dat_validate.py --corpus-scan` (dat_ref.py 0.1.0). One row per DAT found under `corpus/raw/` (loose, or inside a zip read in place); disk images are skipped (another workstream extracts them). Damaged % = damaged record starts / (valid + damaged). crc8 = header CRC-8 matches on CRC-16-valid records (good/bad).

| file | container | bytes | kind | model (code) | build | clock Hz | header SN | app-side | records | damaged % | crc8 | frames (with fix) | UTC start | dur s | status |
|---|---|---:|---|---|---|---:|---|---|---:|---:|---|---:|---|---:|---|
| ios_backup.zip | corpus\raw\cfreds\DF005\2018_June\mobile_iOS\ios_backup.zip | | | | | | | | | | | | | | zip unreadable (incomplete?): File is not a zip file |
| iOS_Backup/6b/6b9006d970dad577b6647b629a8ce6ac70c57263 | corpus\raw\cfreds\DF068\2018_September\mobile_iOS_backup\iOS_Backup.zip | 8928586 | dat_v3 | Unidentified DJI (V3-era DAT, product code 41) (41) | Aug 17 2018 23:33:36 | 3840000 | 0M6DF73001P5JS | True | 106005 | 0.008 | 106005/0 | 8143 (8143) | 2018-09-26T18:31:27.049Z | 1016.832 | decoded |
| gh-cloudedgeaero-djiflightlogs\FLY062.DAT | (loose) | 6467584 | dat_v1 | Unidentified DJI (V1-era DAT, product code 6) (6) | Jan 14 2015 21:27:56 | 600 |  | False | 145488 | 0.000 | 145488/0 | 342 (0) |  | 36.413 | decoded |
| gh-cloudedgeaero-djiflightlogs\FLY063.DAT | (loose) | 16252928 | dat_v1 | Unidentified DJI (V1-era DAT, product code 6) (6) | Jan 14 2015 21:27:56 | 600 |  | False | 366170 | 0.000 | 366170/0 | 864 (0) |  | 88.658 | decoded |
| gh-drdp\dats\20-07-10-08-31-09_FLY256.DAT | (loose) | 2026576 | dat_v3 | Mavic Pro (16) | Nov 16 2017 10:30:14 | 4500000 | 08RDE4E00102YL | True | 34778 | 0.026 | 34778/0 | 2662 (2630) | 2020-07-10T12:30:55.642Z | 386.118 | decoded |
| gh-drop\sample-data\DAT-Files\FLY000.DAT | (loose) | 12976128 | dat_v1 | Unidentified DJI (V1-era DAT, product code 6) (6) | Apr  8 2016 18:22:32 | 600 |  | False | 219372 | 0.000 | 219371/0 | 671 (670) | 2016-09-15T17:55:43.011Z | 67.702 | decoded |
| gh-drop\sample-data\DAT-Files\FLY002.DAT | (loose) | 96927744 | dat_v1 | Unidentified DJI (V1-era DAT, product code 6) (6) | Apr  8 2016 18:22:32 | 600 |  | False | 1632806 | 0.000 | 1632805/0 | 4961 (4900) | 2016-09-26T18:10:31.530Z | 500.107 | decoded |
| gh-drop\sample-data\DAT-Files\FLY003.DAT | (loose) | 69795840 | dat_v1 | Unidentified DJI (V1-era DAT, product code 6) (6) | Apr  8 2016 18:22:32 | 600 |  | False | 1175961 | 0.000 | 1175962/0 | 3574 (3513) | 2016-09-26T18:26:07.530Z | 362.077 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_15-27-26_FLY012.DAT | (loose) | 5534341 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 62800 | 1.070 | 62800/0 | 2715 (2712) | 2023-09-22T19:26:15.139Z | 624.807 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_15-42-10_FLY013.DAT | (loose) | 659589 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 7118 | 8.155 | 7124/0 | 98 (97) | 2023-09-22T19:41:06.610Z | 1108.383 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_15-49-58_FLY014.DAT | (loose) | 6046085 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 71368 | 0.802 | 71373/0 | 352 (351) | 2023-09-22T19:48:29.172Z | 1117.373 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_16-47-21_FLY015.DAT | (loose) | 2522501 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 29226 | 2.185 | 29236/0 | 2461 (2460) | 2023-09-22T20:46:35.672Z | 287.62 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_16-55-52_FLY016.DAT | (loose) | 1632389 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 19013 | 3.153 | 19017/0 | 179 (179) | 2023-09-22T20:55:10.462Z | 192.814 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-04-35_FLY017.DAT | (loose) | 1846405 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 21136 | 2.545 | 21150/0 | 142 (141) | 2023-09-22T21:03:38.845Z | 1106.201 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-12-43_FLY018.DAT | (loose) | 1907845 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 22214 | 2.617 | 22226/0 | 169 (169) | 2023-09-22T21:12:05.365Z | 1117.397 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-19-23_FLY019.DAT | (loose) | 3711109 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 42754 | 1.343 | 42768/0 | 81 (81) | 2023-09-22T21:18:49.095Z | 1106.786 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-35-00_FLY020.DAT | (loose) | 1397125 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 16170 | 3.962 | 16175/0 | 211 (211) | 2023-09-22T21:34:26.407Z | 1117.481 | decoded |
| gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-44-44_FLY021.DAT | (loose) | 2092165 | dat_v3 | Unidentified DJI (V3-era DAT, product code 89) (89) | Jun 16 2023 21:12:36 | 3840000 | 1581F6GKB235K0 | True | 24386 | 2.327 | 24402/0 | 29 (29) | 2023-09-22T21:44:12.217Z | 1117.526 | decoded |

SHA-256 per file:

- `0272e18f0b419e42d97d888f5621f9759bd5c7a1cb26993e7ed2f8cf3ad9ec7c` iOS_Backup/6b/6b9006d970dad577b6647b629a8ce6ac70c57263
- `d8179e4e98d3d9ee55ba0f12b08104baaac08ebfc708aac7fb6401678cde05d1` gh-cloudedgeaero-djiflightlogs\FLY062.DAT
- `81f635518ecda9a67dc0a01d611beee8ea97e96fd929ef642acb6e9ae7280e02` gh-cloudedgeaero-djiflightlogs\FLY063.DAT
- `d04912300e0b4873c458035230011b87e9904e03fd4faaed5f31f638d7c6d4b4` gh-drdp\dats\20-07-10-08-31-09_FLY256.DAT
- `fe9808357b19dd7e9bf02b5c459dc2ce25064a2e1532336889be00c5a1a656f1` gh-drop\sample-data\DAT-Files\FLY000.DAT
- `5ae0d4f99e7e429997f6d664237f7ccd80b7b6b80b38fecdf2502d5760520e39` gh-drop\sample-data\DAT-Files\FLY002.DAT
- `a1870e19f0c44a18e96597098fb148369552414798c3429dadc8e0fbe901a512` gh-drop\sample-data\DAT-Files\FLY003.DAT
- `b55fadc162aef3e68931011d2f9a984900d6bc8fe5074a06cf183547e4108e2a` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_15-27-26_FLY012.DAT
- `539b6cbb2f7511302bb45810f469a81fac05487f396b017ba3aa90cd1a3f2ff7` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_15-42-10_FLY013.DAT
- `276ad2c38a13937a0b2257960e1f56f5962fad6e7fc343863e4fa1461c86da61` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_15-49-58_FLY014.DAT
- `b8fb12d7330c73b43e0fc871caf6de772573df2ba5ab389983ac4c0afb747f77` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_16-47-21_FLY015.DAT
- `4349bd3873c7743777ae00959dea2c413c698b23699c1330a40f9bcb757e7c7f` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_16-55-52_FLY016.DAT
- `ced8fc737cc45ab07668697cf926dcb5402134cff852bf251cd535e8831bc124` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-04-35_FLY017.DAT
- `8b0aa0f26e4a681aa374f29549c0099ae32edafa48ca59a6f2ec9e71dd7df0f7` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-12-43_FLY018.DAT
- `5876ada0c79731c5091432df88fd66bf7952b99bd162b7694adad5c15402e751` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-19-23_FLY019.DAT
- `f91f67673926d1edb925ff5d235d6abb925b74111e2ecc4ee30d6a476244fc04` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-35-00_FLY020.DAT
- `6d46a08629c28fc5c00d8cb166efd19961d86e3f74f040f4e22879d92e022936` gh-isensuiuc-dji-flight-log\FlightRecord0922\MCDatFlightRecords\2023-09-22_17-44-44_FLY021.DAT
