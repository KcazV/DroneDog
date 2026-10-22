# dji-log-parser 0.5.7 -> DroneDog C2/C3 mapping (contract v1.1)

How `dronedog_core.parse_txt` turns the `dji-log-parser` (MIT, pinned `=0.5.7`) data model
into the contract in `ARCHITECTURE.md` (C1-C3). Source: `core/src/txt.rs`.

Pipeline: `DJILog::from_bytes` -> `DJILog::records(keychains)` -> one pass of our own over
the records (`record_pass`, for data the library's `Frame` drops) ->
`frame::records_to_frames` -> C3 columns built directly as typed vectors.

The library emits a `Frame` each time an OSD record arrives, holding the state accumulated
since the previous OSD record. The final OSD record's state is never emitted (one frame
dropped per log; a warning says so). Our record pass uses the same segmentation, so
frame `i` and extra `i` describe the same moment.

"Library unit" is the unit after dji-log-parser's own conversion; "Conversion" is what we
apply on top. `None` is written whenever the source did not provide a value. We never
substitute 0.

## C2 `details` (plaintext Details block, readable for every version incl. v13+)

| C2 key | Source | Conversion / notes |
|---|---|---|
| aircraft_model | `Details.product_type` | Debug name of the enum (`Phantom4Pro`, `Mavic3`, `Unknown(n)`); null if `None` |
| aircraft_name | `Details.aircraft_name` | trimmed, empty -> null |
| aircraft_sn | `Details.aircraft_sn` | as recorded |
| fc_sn | none | null: TXT logs carry no separate flight-controller serial. See `component_serials` |
| camera_sn / rc_sn / battery_sn | `Details.*_sn` | as recorded, empty -> null |
| app_platform | `Details.app_platform` | Debug name (`IOS`, `Android`, `DJIFly`, `Windows`, `Unknown(n)`) |
| app_version | `Details.app_version` | `"x.y.z"`; `"0.0.0"` -> null |
| log_version | prefix byte @0x0A | int |
| start_time_utc | `Details.start_time` (epoch ms) | ISO-8601 `...Z`, millisecond precision; null if year not in 2011..2099 |
| duration_s | `Details.total_time` | library already converts ms -> s |
| total_distance_m | `Details.total_distance` | m |
| max_height_m | `Details.max_height` | m |
| max_h_speed_ms / max_v_speed_ms | `Details.max_horizontal_speed` / `max_vertical_speed` | m/s |
| home_lat / home_lon | `Details.latitude` / `longitude` | deg; null when (0,0) or out of range |
| city / area / sub_street | `Details.city` / `area` / `sub_street` | as recorded (the app writes placeholders such as `Map Loading`; kept verbatim) |
| firmware_version | first `Firmware` record with `sender_type == MC` | `"x.y.z"`; null if none (versions `0.0.0` ignored) |
| record_line_count | `Details.record_line_count` | int |

Extensions (beyond C2, may be absent): `street`, `product_type_code` (same as model,
including `None`), `photo_count` (`capture_num`), `video_time_s`, `firmware_versions`
(map sender -> version from all `Firmware` records), `component_serials` (map from
`ComponentSerial` records), `records_unknown_type` (records framed correctly but of a type
0.5.7 does not decode).

## C3 frame columns

| C3 column | dji-log-parser field | Library unit | Conversion / null rule |
|---|---|---|---|
| t_offset_ms | `Frame.custom.date_time` | UTC, ms | `date_time - first valid date_time` in ms. Null for frames before the first valid app timestamp. The app clock can repeat a value for several frames and may step backwards (warned) |
| t_utc | `Frame.custom.date_time` | UTC | ISO-8601 Z, ms; null if year not in 2011..2099 (library default = 1970) |
| lat, lon | `Frame.osd.latitude/longitude` | deg (library converts from rad) | null when (0,0) within 1e-7 deg, NaN, or out of range |
| alt_rel_m | `Frame.osd.height` | m (OSD `altitude` /10) | height relative to take-off point (barometric) |
| height_m | `Frame.osd.height` | m | same value as alt_rel_m (DJI's "height" field). VPS/ultrasonic height (`osd.vps_height`) is not mapped (see report) |
| alt_msl_m | `Frame.osd.altitude` | m | library computes OSD height + Home altitude. Only emitted once a Home record has been seen with a valid home point, else null. Accuracy is that of the app's home altitude |
| vel_n, vel_e, vel_d | `Frame.osd.x_speed/y_speed/z_speed` | m/s (raw * 0.1) | none. DJI FlightRecordParsingLib documents these as N-E-D, so vel_d is positive downward |
| speed_h_ms | derived | m/s | `sqrt(vel_n^2 + vel_e^2)` |
| roll, pitch, yaw | `Frame.osd.roll/pitch/yaw` | deg (raw * 0.1) | none |
| gimbal_pitch/roll/yaw | `Frame.gimbal.pitch/roll/yaw` | deg (raw * 0.1) | null until the first Gimbal record |
| rc_aileron/elevator/throttle/rudder | `Frame.rc.*` | raw stick channel u16 | `(raw - 1024) / 660` clamped to [-1, 1] (DJI RC range 364..1684, centre 1024). Null until the first RC / RCDisplayField record |
| rc_mode_switch | `Record::RC.flight_mode_switch` (our pass; not in `Frame`) | enum | Debug name `One`/`Two`/`Three` (DJI "Position One/Two/Three"; Mavic Pro remap applied by the library only when product type is passed, which 0.5.7's `records()` does not do). Null until first RC record |
| flight_mode | `Frame.osd.flyc_state` | enum | Debug name (`GPSAtti`, `GoHome`, `AutoLanding`, `Unknown(n)`) |
| gps_sats | `Frame.osd.gps_num` | count | int |
| gps_health | `Frame.osd.gps_level` | 0..5 | int |
| battery_pct | `Frame.battery.charge_level` | % | null until a CenterBattery / SmartBattery / SmartBatteryDynamic record |
| battery_v | `Frame.battery.voltage` | V (library /1000) | same null rule as battery_pct |
| battery_cells_v | `Frame.battery.cell_voltages` | V | only when measured (CenterBattery or SmartBatterySingleVoltage seen and `is_cell_voltage_estimated == false`). The library's estimate (pack V / cell count) is never exported |
| battery_temp_c | `Frame.battery.temperature` (SmartBatteryDynamic, raw/10) else `Record::CenterBattery.temperature` (v8+, raw/10 - 273.15, our pass) | deg C | null if neither record type seen |
| home_lat, home_lon | `Frame.home.latitude/longitude` | deg | null until a Home record with a valid point |
| distance_m | derived | m | haversine (R = 6 371 008.8 m) between home point and current position; null if either is missing. Not the app's own `Custom.distance` |
| motor_on | `Frame.osd.is_motor_on` | bool | none |
| is_flying | `!Frame.osd.is_on_ground` | bool | OSD ground_or_sky in {2,3} |
| vps_height_m (v1.1) | `Frame.osd.vps_height` (OSD `s_wave_height`) | m (raw u8 / 10) | only while `osd.is_swave_work` is true (sensor reports a valid reading, typically below ~10 m); else None |
| distance_app_m (v1.1) | `Record::Custom.distance` (our pass; not in `Frame`) | m, f32 as recorded | value of the latest Custom record; None until the first one. **Semantics differ from `distance_m`:** in the v8 and v11 corpus logs it is the cumulative path distance flown (its last value equals `details.total_distance_m` within 1 m), not distance from home. In v3 samples it tracks distance from home near the end of the log but not elsewhere; unverified for v<6. Exported verbatim; interpret per log version |
| rc_signal_up_pct / rc_signal_down_pct (v1.1) | `Frame.rc.uplink_signal` / `downlink_signal` (OFDM record, 7-bit percent) | % | None until an OFDM record of that direction. None in all corpus logs seen so far (none contained OFDM records) |

## C1 events

Derived per frame from transitions (frame `i` vs `i-1`) plus raw app messages. Each event
gets `t_offset_ms` of its frame (gaps filled with the nearest earlier known offset).

| kind | severity | trigger |
|---|---|---|
| flight_mode | info | first frame's flight mode |
| flight_mode_change | info | `osd.flyc_state` changed |
| rth | warn | mode changed to a `*GoHome*` mode, or `go_home_status` left `Standby` |
| rth_status / rth_end | info | `go_home_status` changed while active / back to `Standby` |
| signal_loss_rth | critical | `osd.flight_action` became `OutOfControlGoHome` |
| low_battery_action | critical | `flight_action` names voltage/power/battery (e.g. `SmartPowerGoHome`, `SeriousLowVoltageLanding`) |
| auto_landing | warn | `flight_action` names landing (e.g. `AppAutoLanding`, `VertLowLimitLanding`) |
| flight_action | info | any other `flight_action` change away from `None` |
| low_battery | warn (level 1) / critical (>=2) | `osd.voltage_warning` rose above 0 or changed level |
| motor_start / motor_stop | info | `osd.is_motor_on` edge (also "already running at log start") |
| takeoff / landing | info | `osd.is_on_ground` edge (also "already airborne at log start") |
| gps_health_drop / gps_health_recovered | warn / info | `gps_level` crossed below / back to >= 3 |
| gps_lost | warn | `osd.is_gpd_used` true -> false (`raw` = `non_gps_cause`) |
| signal_loss / signal_restored | critical / info | OFDM uplink/downlink % went to 0 / back above 0; OSD `rc_outcontrol` set / cleared (our pass) |
| aircraft_fault | warn or critical | rising edge of OSD flags: compass_error, barometer_dead_in_air, motor_blocked, not_enough_force, propeller_catapult, accelerometer_over_range, vibrating, out_of_limit, obstacle_sensor_error |
| motor_start_failed | warn | `motor_start_failed_cause` changed to something other than `None` |
| app_tip / app_warning / app_serious_warning | info / warn / critical | `AppTip` / `AppWarn` / `AppSeriousWarn` record text (`raw` = message). Messages that decode to unreadable bytes are dropped and counted in a warning |

The library's synthetic `Frame.app.tip` strings ("Flight mode changed to ...", gimbal
endpoint tips) are not used; we derive the same facts from fields directly.

## Record counts

`records_total` = every record the library returned. `records_failed` = `Record::Invalid`
(bytes it had to skip to resynchronise). `records_decoded` = total - failed - unknown-type.
When more than 5% fail, a warning states frames/events are incomplete.

## v13+ (AES)

Without `keychains_json`: details only, `frames` all empty lists, `events` empty, counts 0,
`encrypted_unreadable = True`, plain warning. With `keychains_json` (JSON
`[[{"featurePoint": ..., "aesKey": b64, "aesIv": b64}, ...], ...]`, the format
dji-log-parser serialises `KeychainFeaturePoint` in) the same pipeline as v<=12 runs, fully
offline. An empty list is treated as not supplied.
