# DAT reference decoder vs DatCon: validation

Generated 2026-10-21 22:40 UTC by `tools/dat_validate.py` (dat_ref.py 0.1.0). Ground truth: DataverseNO doi:10.18710/AFDYLS (CC0), DJI Phantom 4 Pro flight-controller DATs with the DatCon CSVs the dataset authors produced.

Method: every DatCon CSV row carries `Tick#`, the tick of the record after which DatCon printed the row. dat_ref.py decodes the same DAT and snapshots its decoded state at exactly those ticks; each mapped field is then compared numerically. A row is *tick-matched* when that tick exists in dat_ref's accepted record stream. Values count as matching when the absolute error is within the listed tolerance (0 for integers; float32 rounding level for floats). Rows where one side is empty are counted separately and are not errors by themselves (DatCon suppresses values until its own validity flags are set).

## Per file

| DAT | model | DatCon | DatCon rows | tick-matched | CRC fails | UTC start (dat_ref) | DAT sha256 (first 16) |
|---|---|---|---:|---:|---:|---|---|
| FLY134.DAT | Unidentified DJI (V3-era DAT, product code 35) (code 35) | ConvertDatV3|4.0.5 | 10993 | 99.99% | 0 | 2021-04-19T09:24:52.784Z | e73916fa3e792f0c |
| FLY135.DAT | Unidentified DJI (V3-era DAT, product code 35) (code 35) | ConvertDatV3|4.0.5 | 8556 | 99.99% | 0 | 2021-04-19T09:44:18.927Z | c86ce39e27ede62f |
| FLY140.DAT | Unidentified DJI (V3-era DAT, product code 35) (code 35) | ConvertDatV3|4.0.5 | 6552 | 99.98% | 0 | 2021-04-20T09:40:51.771Z | dce722515f8ae6db |
| FLY168.DAT | Unidentified DJI (V3-era DAT, product code 35) (code 35) | ConvertDatV3|4.0.5 | 6962 | 99.99% | 0 | 2021-05-03T14:35:54.710Z | 0af7e0e83f91f4c0 |
| FLY179.DAT | Unidentified DJI (V3-era DAT, product code 35) (code 35) | ConvertDatV3|4.0.5 | 9525 | 99.99% | 0 | 2021-06-03T06:31:59.179Z | 57158c0b95a8cd09 |
| FLY182.DAT | Unidentified DJI (V3-era DAT, product code 35) (code 35) | ConvertDatV3|4.0.5 | 8546 | 99.99% | 0 | 2021-06-07T13:11:13.667Z | d8c592cc7bc25f5a |

## Per field (all files)

| field | DatCon column | C3 column | files | values compared | within tol | % | max abs err | tol | DatCon-only | ours-only |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lat (IMU_ATTI0) | `IMU_ATTI(0):Latitude` | lat | 6 | 51012 | 51012 | 100.0000 | 7.11e-15 | 1e-09 | 0 | 116 |
| lon (IMU_ATTI0) | `IMU_ATTI(0):Longitude` | lon | 6 | 51012 | 51012 | 100.0000 | 8.88e-16 | 1e-09 | 0 | 116 |
| numSats (IMU_ATTI0) | `IMU_ATTI(0):numSats` | gps_sats | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| baro raw | `IMU_ATTI(0):press:D` |  | 6 | 51128 | 51128 | 100.0000 | 1.52e-05 | 0.0001 | 0 | 0 |
| velN | `IMU_ATTI(0):velN` | vel_n | 6 | 51128 | 51128 | 100.0000 | 4.77e-07 | 1e-06 | 0 | 0 |
| velE | `IMU_ATTI(0):velE` | vel_e | 6 | 51128 | 51128 | 100.0000 | 3.89e-07 | 1e-06 | 0 | 0 |
| velD | `IMU_ATTI(0):velD` | vel_d | 6 | 51128 | 51128 | 100.0000 | 2.38e-07 | 1e-06 | 0 | 0 |
| velH | `IMU_ATTI(0):velH:C` | speed_h_ms | 6 | 51128 | 51128 | 100.0000 | 5.37e-07 | 1e-06 | 0 | 0 |
| roll | `IMU_ATTI(0):roll:C` | roll | 6 | 51128 | 51128 | 100.0000 | 7.11e-15 | 1e-06 | 0 | 0 |
| pitch | `IMU_ATTI(0):pitch:C` | pitch | 6 | 51128 | 51128 | 100.0000 | 7.11e-15 | 1e-06 | 0 | 0 |
| yaw | `IMU_ATTI(0):yaw:C` | yaw | 6 | 51128 | 51128 | 100.0000 | 5.68e-14 | 1e-06 | 0 | 0 |
| accel X | `IMU_ATTI(0):accelX` |  | 6 | 51128 | 51128 | 100.0000 | 1.49e-08 | 1e-07 | 0 | 0 |
| mag X | `IMU_ATTI(0):magX` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| IMU temp | `IMU_ATTI(0):temperature` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 1e-09 | 0 | 0 |
| distanceHP | `IMU_ATTI(0):distanceHP:C` | distance_m | 6 | 0 | 0 | 0.0000 | 0 | 1e-06 | 0 | 50972 |
| relativeHeight (OSD) | `osd_data:relativeHeight` | alt_rel_m | 6 | 51128 | 51128 | 100.0000 | 7.63e-07 | 1e-06 | 0 | 0 |
| vpsHeight (OSD) | `osd_data:vpsHeight` |  | 6 | 13882 | 13882 | 100.0000 | 3.81e-07 | 1e-06 | 0 | 37246 |
| flightTime (OSD) | `osd_data:flightTime` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| navHealth/gpsHealth | `osd_data:navHealth` | gps_health | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| flyCState | `osd_data:flyCState` | flight_mode | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| GPS lat | `GPS:Lat` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 1e-09 | 0 | 0 |
| GPS lon | `GPS:Long` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 1e-09 | 0 | 0 |
| GPS date | `GPS:Date` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| GPS time | `GPS:Time` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| GPS heightMSL | `GPS:heightMSL` | alt_msl_m | 6 | 51128 | 51128 | 100.0000 | 1.51e-05 | 0.0001 | 0 | 0 |
| GPS hDOP | `GPS:hDOP` |  | 6 | 51128 | 0 | 0.0000 | 151 | 1e-06 | 0 | 0 |
| GPS numGPS | `GPS:numGPS` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| GPS numSV | `GPS:numSV` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| GPS velN | `GPS:velN` |  | 6 | 51128 | 51128 | 100.0000 | 4.58e-07 | 1e-06 | 0 | 0 |
| ctrl_roll raw (recDef) | `Controller:ctrl_roll:D` |  | 6 | 51128 | 51128 | 100.0000 | 2.97e-08 | 1e-07 | 0 | 0 |
| ctrl_pitch raw (recDef) | `Controller:ctrl_pitch:D` |  | 6 | 51128 | 51128 | 100.0000 | 2.98e-08 | 1e-07 | 0 | 0 |
| ctrl_thr raw (recDef) | `Controller:ctrl_thr:D` |  | 6 | 51128 | 51128 | 100.0000 | 2.98e-08 | 1e-07 | 0 | 0 |
| ctrl_yaw raw (recDef) | `Controller:ctrl_yaw:D` |  | 6 | 51128 | 51128 | 100.0000 | 2.98e-08 | 1e-07 | 0 | 0 |
| mode_switch raw (recDef) | `Controller:mode_switch:D` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| BatVol mV (recDef) | `BatteryInfo:BatVol:D` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| BatCurrent mA (recDef) | `BatteryInfo:BatCurrent:D` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| CapPercnt (recDef) | `BatteryInfo:CapPercnt:D` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| BatTemp (recDef) | `BatteryInfo:BatTemp:D` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| cell1 mV (recDef) | `bat_dynamic_data:cell1:D` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| cell4 mV (recDef) | `bat_dynamic_data:cell4:D` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| C3 battery_pct | `BatteryInfo:CapPercnt:D` | battery_pct | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| C3 battery_v | `BatteryInfo:BatVol:D` | battery_v (mV) | 6 | 51128 | 51128 | 100.0000 | 0 | 0.5 | 0 | 0 |
| motor RF rpm | `Motor:Speed:RFront` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| motor RB rpm | `Motor:Speed:RBack` |  | 6 | 51128 | 51128 | 100.0000 | 0 | 0 | 0 | 0 |
| motor RF current | `Motor:Current:RFront` |  | 6 | 51128 | 51128 | 100.0000 | 4.58e-07 | 1e-05 | 0 | 0 |

## Worst residuals

- GPS hDOP: FLY140.DAT tick 66184121 DatCon=`1.53` dat_ref=`153.0`

## Known, explained differences

- `GPS hDOP`: DatCon 4.0.5 prints the stored float / 100 on code-35 firmware (stored 96.0 -> 0.96). dat_ref keeps the stored value; hDOP is not a C3 column.
- `DatCon-only` / `ours-only` count rows where one tool leaves the cell empty (DatCon suppresses IMU position until its GPS-valid flag is set; DatCon 4 has no home point / distanceHP for these files). They are not value errors.
- The ~0.01% of DatCon rows that are not tick-matched fall on records dat_ref excludes by the sequence rule or on the final record of the file.
- t_utc (dat_ref, interpolated) minus DatCon's whole-second `GPS:dateTimeStamp` on the same rows: -0.07..+1.19 s (FLY134), -0.07..+2.67 s (FLY168; larger values where the GPS record is stale).

## Internal consistency (not DatCon)

Max |OSD type-12 attitude (0.1 deg) - quaternion attitude from type 2048| on rows where both records are within 50 ms:

- FLY134.DAT: pitch 2.94, roll 0.95, yaw 0.61 deg (n=5619)
- FLY135.DAT: pitch 6.75, roll 4.36, yaw 6.94 deg (n=4352)
- FLY140.DAT: pitch 3.74, roll 1.85, yaw 5.54 deg (n=3348)
- FLY168.DAT: pitch 2.43, roll 2.58, yaw 5.15 deg (n=3556)
- FLY179.DAT: pitch 2.78, roll 2.97, yaw 5.17 deg (n=4838)
- FLY182.DAT: pitch 3.12, roll 3.04, yaw 5.46 deg (n=4383)
