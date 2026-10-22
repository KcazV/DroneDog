# DroneDog event glossary

Plain-English meanings of the DJI flight-log event lines DroneDog explains. Generated from the rule table in `dronedog/analysis/explain.py` (`python -m dronedog.analysis.explain > docs/EVENT_GLOSSARY.md`); do not edit by hand.

How to read this: the original log text is always kept and shown verbatim. The explanation states only what the line itself establishes; where a field is undocumented the explanation says so. Each row shows the rule id, the matching pattern (regular expression applied after the leading flight-controller tick, any `<FLY>`/`{task}INFO:` prefix and the `[L-MODULE]` tag are removed), a real example line from the test corpus, and the explanation DroneDog produces for that example.

Importance: **high** = forensically significant (take-off, landing, motor start/stop, home point, return-to-home, low battery, GPS or signal loss, no-fly zones, forced landing); **normal** = useful context; **low** = start-up noise and configuration dumps.

## Flight

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.takeoff` | any (record kind: takeoff) | `^Aircraft airborne$` | `Aircraft airborne` | Take-off: aircraft left the ground | DroneDog derived this from the app log: the aircraft's 'on ground' flag changed to false, i.e. it took off. | high |
| `txt.takeoff_at_start` | any (record kind: takeoff) | `^Aircraft already airborne at log start$` | `Aircraft already airborne at log start` | Aircraft already airborne when log began | The first record of the app log shows the aircraft already in the air, so the take-off itself is not in this log. | high |
| `txt.landing` | any (record kind: landing) | `^Aircraft on ground$` | `Aircraft on ground` | Landing: aircraft on the ground | DroneDog derived this from the app log: the aircraft's 'on ground' flag changed to true, i.e. it landed (or was set down). | high |
| `txt.initial_mode` | any | `^Initial flight mode (?P<mode>\w+(?:\(\d+\))?)$` | `Initial flight mode GPSAtti` | Flight mode at log start: P-GPS | The first record of the app log shows flight mode 'GPSAtti' (normal GPS-assisted flight (positioning mode): the aircraft holds position when the sticks are released). | normal |
| `txt.mode_change` | any | `^Flight mode changed from (?P<from>[\w()]+) to (?P<to>[\w()]+)$` | `Flight mode changed from GPSAtti to GPSSport` | Flight mode: P-GPS -> Sport | The app log shows the flight mode changing from 'GPSAtti' (normal GPS-assisted flight (positioning mode): the aircraft holds position when the sticks are released) to 'GPSSport' (Sport mode: higher speeds; obstacle sensing may be disabled on many models). | normal |
| `txt.rth_start` | any | `^Return-to-home started \(status (?P<status>.+)\)$` | `Return-to-home started (status Cruise)` | Return-to-home started | The app log's return-to-home status left 'Standby': the aircraft began returning to its home point (first status: Cruise, flying back towards home). | high |
| `txt.rth_end` | any | `^Return-to-home status back to Standby$` | `Return-to-home status back to Standby` | Return-to-home ended | The app log's return-to-home status returned to 'Standby': the return-to-home sequence ended (completed, cancelled or replaced by landing). | high |
| `txt.rth_status` | any | `^Return-to-home status (?P<status>.+)$` | `Return-to-home status Ascending` | Return-to-home phase: Ascending | The app log's return-to-home status changed to 'Ascending' (climbing to RTH altitude). | normal |
| `txt.fc_action` | any | `^Flight controller action: (?P<action>[\w()]+)$` | `Flight controller action: OutOfControlGoHome` | Signal-lost return-to-home (failsafe) | Return-to-home started because the remote-controller signal was lost (failsafe RTH). (App-log flight action 'OutOfControlGoHome'.) | high |
| `flymode.hover` | [L-FLYMODE] | `^SWITCH2HOVER!` | `23663 [L-FLYMODE]SWITCH2HOVER!` | Switched to hover (position hold) | The flight-mode logic put the aircraft into its hover/hold state, holding its current position and height when positioning is available. It is logged routinely (for example after take-off, after an automatic manoeuvre ends, or when motors stop); the line itself does not state the cause. | normal |
| `flymode.gps2atti` | [L-FLYMODE] | `^GPS2ATTI!` | `[L-FLYMODE]GPS2ATTI!Init atti dynscale scale 0.000000` | Switched from GPS mode to ATTI mode | The flight controller changed from GPS-assisted control (position hold) to ATTI (attitude-only: it stays level and holds height but drifts with the wind). This appears at power-on before satellite positioning is usable; during flight it means GPS positioning was lost or rejected. | high |
| `flymode.req_force_landing` | [L-FLYMODE] | `^Req force landing!` | `[L-FLYMODE]Req force landing!` | Forced landing requested | A forced (immediate) landing was requested of the flight-mode logic. | high |
| `flymode.recv_force_landing` | [L-FLYMODE] | `^Recv force landing cmd!\((?P<a>\d+),(?P<b>\d+),(?P<c>\d+)\)` | `[L-FLYMODE]Recv force landing cmd!(0,0,1)` | Forced-landing command received | The flight-mode logic received a forced-landing command (internal arguments 0,0,1, not documented). | high |
| `flymode.reset_force_landing` | [L-FLYMODE] | `^reset all force landing cmd` | `[L-FLYMODE]reset all force landing cmd` | Forced-landing requests cleared | All pending forced-landing commands were reset (cleared). | normal |
| `flymode.rc_force_landing` | [L-FLYMODE] | `^rc2\w+ sets force landing!` | `[L-FLYMODE]rc2wm220 sets force landing!` | Remote controller set forced landing | The remote-controller interface set a forced landing (for example the pilot holding the landing control). | high |
| `flymode.app_force_landing` | [L-FLYMODE] | `^App sets force landing!` | `[L-FLYMODE]App sets force landing!` | App set forced landing | The mobile app commanded a forced landing. | high |
| `flymode.rc_assisted_takeoff` | [L-FLYMODE] | `^MR_REQ_R_RC_ASST_TAKEOFF` | `[L-FLYMODE]MR_REQ_R_RC_ASST_TAKEOFF` | Assisted take-off requested from remote controller | The flight-mode logic received a remote-controller request for assisted take-off (the aircraft climbs away from the ground under automatic control after motor start). | high |
| `flymode.ctrl_request` | [L-FLYMODE] | `\[Ctrl<(?P<n>\d+)>\]\s*REQ_(?P<source>[A-Z0-9_]+)\s+(?P<mode>[A-Z0-9_]+)\s+ctrl_(?P<ctrl>\w+)` | `[L-FLYMODE][Ctrl<1>] REQ_RC_NORMAL ATTI_HOLD ctrl_gps_atti` | Control mode: GPS position hold | The flight controller granted control-mode request 'ATTI_HOLD' (handler ctrl_gps_atti) from remote-controller sticks; control slot 1. | normal |
| `flymode.ctrl_reset` | [L-FLYMODE] | `^CTRL reset all by (?P<reason>.+?)\s*$` | `[L-FLYMODE]CTRL reset all by rc mode switch` | Control state reset: rc mode switch | The flight-mode logic reset its control requests because of: rc mode switch. | low |
| `flymode.rc_mode_switch` | [L-FLYMODE], [L-RC] | `^(?:\[RC\] )?mode switch changed!?` | `[L-FLYMODE][RC] mode switch changed` | Flight-mode switch moved on remote controller | The flight-mode switch on the remote controller changed position (for example between P/S/A or N/S/T positions). | normal |
| `flymode.landing_reset` | [L-FLYMODE] | `^\[LANDING\] reset landing status` | `[L-FLYMODE][LANDING] reset landing status` | Landing status reset | The flight-mode logic reset its landing state. | low |
| `takeoff.alt` | [L-TAKEOFF] | `^alti:\s*(?P<alt>-?[\d.]+)\s+tors:\s*(?P<yaw>-?[\d.]+)` | `[L-TAKEOFF]alti: 63.761677 tors: -44.675697` | Take-off recorded (altitude 63.761677 m) | The take-off module recorded the altitude at take-off (63.761677 m, the flight controller's altitude reference) and the heading ('tors', DJI's term for the yaw axis) of -44.675697 degrees. DatCon uses this line as the take-off altitude. | high |
| `takeoff.rtk_alt` | [L-TAKEOFF] | `^RTK alti:\s*(?P<alt>-?[\d.]+)` | `[L-TAKEOFF] RTK alti: 445.702484` | Take-off RTK altitude 445.702484 m | The take-off module recorded the RTK (high-precision GNSS) altitude at take-off: 445.702484 m. | high |
| `takeoff.legacy_alt` | any | `^\[TO\.ALT\s*\]\s*(?P<alt>-?[\d.]+)` | `[TO.ALT ] -42.983475` | Take-off recorded (altitude -42.983475 m) | Older-firmware take-off line: altitude at take-off -42.983475 m (flight-controller altitude reference). | high |
| `takeoff.auto_enter` | [L-TAKEOFF] | `^enter auto takeoff plan` | `[L-TAKEOFF]enter auto takeoff plan` | Automatic take-off started | The flight controller started its automatic take-off sequence (typically commanded from the app). | high |
| `takeoff.auto_exit` | [L-TAKEOFF] | `^exit auto takeoff plan` | `[L-TAKEOFF]exit auto takeoff plan!` | Automatic take-off sequence ended | The automatic take-off sequence ended (normally because the target height was reached). | normal |
| `takeoff.assist_enter` | [L-TAKEOFF] | `^enter assit takeoff plan` | `[L-TAKEOFF]enter assit takeoff plan` | Assisted take-off started | The flight controller started an assisted take-off (automatic climb away from the ground after the pilot's command). | high |
| `takeoff.assist_exit` | [L-TAKEOFF] | `^exit assit takeoff plan` | `[L-TAKEOFF]exit assit takeoff plan` | Assisted take-off sequence ended | The assisted take-off sequence ended. | normal |
| `fsm.state` | [L-FMU/FSM] | `^state changed\.\s*cur:\s*(?P<state>.+?)\s*$` | `[L-FMU/FSM]state changed. cur: in air` | Aircraft airborne (flight state: in air) | The flight controller's state machine changed to 'in air': it considers the aircraft to have taken off. | high |
| `fsm.near_ground` | [L-FMU/FSM] | `^near ground` | `[L-FMU/FSM]near ground` | Aircraft near the ground | The flight state machine judged the aircraft to be near the ground (logged around take-off, landing and while on the ground). | normal |
| `fsm.not_near_ground` | [L-FMU/FSM] | `^not near ground` | `[L-FMU/FSM]not near ground` | Aircraft no longer near the ground | The flight state machine judged the aircraft to be clear of the ground (it has climbed away). | normal |
| `rc.start_landing` | [L-RC] | `^Start landing\.\s*rc vib!` | `[L-RC]Start landing. rc vib!` | Landing started (controller vibrates) | The flight controller started a landing and told the remote controller to vibrate to alert the pilot. | high |
| `rc.emergency_brake` | [L-RC] | `^Emergency break\.\s*rc vib!` | `[L-RC]Emergency break. rc vib!` | Emergency brake (controller vibrates) | The flight controller logged an emergency brake: the aircraft is commanded to stop and hover, and the remote controller vibrates as an alert. The line does not say whether the pilot (pause/brake button) or an automatic protection triggered it. | high |
| `nmis.gohome_failed` | [L-N_MIS] | `^GoHome Check Failed: (?P<why>.+?)!?\s*$` | `[L-N_MIS]GoHome Check Failed: motor off!` | Return-to-home request rejected: motor off | A return-to-home request was rejected by the flight-task manager because: motor off. | high |
| `nmis.abort_all` | [L-N_MIS] | `^req abort all for (?P<why>.+?)\s*$` | `[L-N_MIS]req abort all for exit landing for ground not smooth` | Landing aborted: ground not suitable | The flight controller aborted its automatic landing because it judged the ground below not smooth/level enough (landing protection). The aircraft stays airborne until the pilot acts. | high |
| `nmis.internal` | [L-N_MIS] | `^(?:send cur mission exit data\|Switch new mis\|Exit mis\|Init plan\|Fail to check mis\|Check fail of plan\|Exit plan\|req abort plan for\|reset_smart_landing_status)` | `[L-N_MIS]Switch new mis 4 req 2 from mis 3 req 2` | Flight-task manager internal step | Internal bookkeeping of the flight-task manager (switching between numbered internal tasks/plans; the numbers are internal IDs). | low |
| `api.force_landing` | [L-API] | `^basic_cmd_force_landing(?P<variant>_avoid_ground)?$` | `[L-API]basic_cmd_force_landing` | Forced landing command | The SDK/app interface executed a forced-landing command. | high |
| `api.exit_landing` | [L-API] | `^basic_cmd_exit_landing` | `[L-API]basic_cmd_exit_landing` | Landing cancelled by command | The SDK/app interface executed a command to exit (cancel) landing. | high |
| `api.landing_checks` | [L-API] | `^(?:force_landing_check\|force_landing_avoid_ground_check\|exit_landing_check)` | `[L-API]force_landing_check` | Landing command pre-check | The SDK/app interface checked whether a forced-landing or exit-landing command may be executed. | normal |
| `ctrl.init` | [L-CTRL] | `^(?:horiz vel complement filter\|navi_data init\|Wind estimate\|init_DFT)` | `[L-CTRL]Wind estimate using method V2:acc to speed!!` | Flight-control start-up configuration | Start-up message from the flight-control (attitude/velocity controller) module. | low |
| `gear.state` | any | `^Gear \[(?P<value>-?\d+)\] (?P<state>\w+)` | `Gear [-60] Show` | Landing-gear message: Show | Landing-gear (retractable undercarriage) message: 'Show' with value -60. | normal |

## Navigation

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `fdi.ns_init_pass` | [L-FDI] | `^NS\((?P<ns>\d+)\):\s*ns init pass` | `[L-FDI]NS(0): ns init pass` | Navigation system initialised | Navigation system 0 (attitude/position estimator) passed its initialisation. | low |
| `fdi.ns_init_values` | [L-FDI] | `^NS\((?P<ns>\d+)\):\s*ns init (?P<which>[awm])=\s*(?P<x>-?[\d.]+)\s+(?P<y>-?[\d.]+)\s+(?P<z>-?[\d.]+)` | `[L-FDI]NS(0): ns init a= 0.038 -0.007 -1.008` | Navigation init sensor reading (a) | Sensor reading used to initialise navigation system 0: 'a' (a = accelerometer, w = gyroscope, m = magnetometer) = 0.038, -0.007, -1.008. | low |
| `fdi.ns_wait_static` | [L-FDI] | `^NS\((?P<ns>\d+)\):\s*init: wait_static` | `[L-FDI]NS(0): init: wait_static` | Navigation waiting for aircraft to be still | Navigation system 0 is waiting for the aircraft to be stationary before initialising. | low |
| `fdi.no_ns` | [L-FDI] | `^fatal error: no ns with level < (?P<level>\d+)` | `[L-FDI]fatal error: no ns with level < 4 !!!` | No usable navigation solution | Fault detection reports that no navigation-system instance had a fault level below 4 (DJI's internal scale), i.e. no acceptable position/attitude estimate was available at that moment. | high |
| `fdi.ns_switch_warning` | [L-FDI] | `^ns switch app warning!` | `[L-FDI]ns switch app warning!` | Navigation source switch warning | Fault detection switched navigation source and raised a warning to the app. | high |
| `fdi.ns_req` | [L-FDI] | `^ns req:(?P<who>\w+),(?P<from>\d+)to(?P<to>\d+),reason:(?P<reason>\w+),result:(?P<result>\w+)` | `[L-FDI]ns req:fdi,0to1,reason:normal,result:succeed` | Navigation system switch 0 -> 1 (succeed) | A request by 'fdi' to switch the active navigation system from 0 to 1 (reason 'normal') finished with result 'succeed'. | normal |
| `fdi.switch` | [L-FDI] | `^\[SWITCH\] req:(?P<req>\d+),(?P<from>\d+)->(?P<to>\d+),result:(?P<res>\d+)` | `[L-FDI][SWITCH] req:2,0->1,result:6,serr:3,derr:11` | Navigation switch request 0 -> 1 | Navigation/sensor switch request 2 from 0 to 1, result code 6 (codes not publicly documented). | normal |
| `ns.baro_reset` | [L-NS] | `^\[AHRS\]\s*reset baro just after (?P<ev>takeoff\|land)` | `[L-NS][AHRS] reset baro just after takeoff` | Barometer reference reset after takeoff | The navigation filter reset its barometric height reference just after takeoff (it re-zeroes height relative to the take-off/landing point). | normal |
| `ns.init_attitude` | [L-NS] | `^\[AHRS\]\s*Init atti RPY:\s*\(\s*(?P<roll>-?[\d.]+),\s*(?P<pitch>-?[\d.]+),\s*(?P<yaw>-?[\d.]+)\)` | `[L-NS][AHRS]Init atti RPY: (2.16, 0.19,-105.93)` | Initial attitude: yaw -105.93 deg | The navigation filter initialised the aircraft attitude: roll 2.16, pitch 0.19, yaw (heading) -105.93 degrees. | normal |
| `ns.wmm` | [L-NS] | `^\[AHRS\]\s*wmm (?P<which>dec\|inc):\s*(?P<deg>-?[\d.]+)` | `[L-NS][AHRS] wmm dec: -5.391582` | Magnetic dec from World Magnetic Model: -5.391582 deg | The navigation filter looked up the local magnetic field from the World Magnetic Model: dec = -5.391582 degrees (dec = declination, inc = inclination). Because these depend on location, they are consistent only with certain regions. | low |
| `ns.yaw_aligned` | [L-NS] | `^\[AHRS\]\s*yaw aligned (?P<value>\d)` | `[L-NS][AHRS] yaw aligned 1` | Heading aligned | The navigation filter reports its heading (yaw) estimate aligned (flag 1). | normal |
| `ns.connect` | [L-NS] | `^\[AHRS\]\s*connect (?P<sensor>\w+)\s*:\s*(?P<value>\d)` | `[L-NS][AHRS] connect gps \t :1` | Navigation input connected: gps | The navigation filter registered input 'gps' (gps, rtk, mag = compass, us = ultrasonic, vo = vision) with flag 1. | low |
| `ns.buffer` | [L-NS] | `^\[AHRS\],?\s*(?:qg\|pva) obv` | `[L-NS][AHRS],qg obv buffer size[218/270]` | Navigation filter buffer/configuration status | Internal navigation-filter observation buffer or sensor-timing status (diagnostic). | low |
| `ns.param` | [L-NS] | `^\[AHRS\]\s*(?:para:\s*)?(?:[A-Za-z_][\w ]*?\|R\w+\|Init\s+qg\d\|product\|version(?: code)?\|vo data type\|VO DT changed\|verti id)\s*:\|^\[AHRS\]\s*height max obv num` | `[L-NS][AHRS]  fs_gnss_hp        :  1` | Navigation filter (AHRS) parameter | Start-up configuration value of the navigation filter (attitude/heading reference system), e.g. sensor-fusion switches, gains or version. | low |
| `mis.wp_upload` | [L-MIS] | `^\[WP_UPLOAD\](?P<index>\d+):\s*Llh\((?P<lat_rad>-?[\d.]+),(?P<lon_rad>-?[\d.]+),(?P<alt>-?[\d.]+)\)(?:\s*D:(?P<d>-?[\d.]+))?` | `[L-MIS][WP_UPLOAD]0: Llh(0.81195163,0.11461895,23.30) D:0.21` | Waypoint 0 uploaded (46.521402, 6.567182, 23.30 m) | A waypoint-mission point was uploaded to the aircraft: latitude/longitude logged in radians (0.81195163, 0.11461895) = about 46.521402, 6.567182; altitude 23.30 m (DJI waypoint altitudes are relative to the take-off point). The 'D' value 0.21 is unlabelled. | normal |
| `mis.sif_header` | [L-MIS] | `^\\|#SiF%\\|Date` | `[L-MIS]\|#SiF%\|Date\t\tTime\t\tID\tLat\t\t\t\tLon\t\t\tAlt\t\tPress\tYaw\t\tRTKValid` | Mission position-record header | Header of the tab-separated mission position records that follow (Date, Time, ID, Lat, Lon, Alt, Press, Yaw, RTKValid). | low |
| `mis.sif_record` | [L-MIS] | `^\\|#SiF%\\|(?P<date>\d{8})\t+(?P<time>\d{6})\t+\s*(?P<id>\d+)\t+(?P<lat>-?[\d.]+)\t+(?P<lon>-?[\d.]+)\t+(?P<alt>-?[\d.]+)\t+(?P<press>-?[\d.]+)\t+(?P<yaw>-?[\d.]+)\t+(?P<rtk_valid>\d+)` | `[L-MIS]\|#SiF%\|20210419\t095119\t     0\t46.52140393\t\t6.56718225\t469.05\t360.17\t0.03\t1` | Mission position record (46.52140393, 6.56718225) | Tab-separated record from the mission module (header: Date Time ID Lat Lon Alt Press Yaw RTKValid): 2021-04-19 09:51:19 (time zone not stated), position 46.52140393, 6.56718225, Alt 469.05, Press 360.17, Yaw 0.03, RTK valid 1. In the corpus about one record is written per mission camera shot. | normal |
| `mis.mission_received` | [L-MIS] | `^received mission length (?P<count>\d+) from app` | `[L-MIS]received mission length 5 from app` | Waypoint mission received from app (5 points) | The aircraft received a waypoint mission of 5 waypoints from the app. | normal |
| `mis.wp_info_main` | [L-MIS] | `^\[WP INFO\]:\s*len:(?P<count>\d+), vel_range:(?P<max_speed>[\d.]+), idle_vel:(?P<speed>[\d.]+), action_on_finish:(?P<finish_action>\d+)` | `[L-MIS][WP INFO]: len:5, vel_range:14.000000, idle_vel:13.000000, action_on_finish:1` | Waypoint mission settings: 5 points, speed 13.000000 | Waypoint mission parameters: 5 waypoints, cruise speed 13.000000 (maximum 14.000000; consistent with m/s), finish-action code 1. | normal |
| `mis.go_stop` | [L-MIS] | `^WP Mission: received go/stop (?P<value>\d+) from app` | `[L-MIS]WP Mission: received go/stop 0 from app` | Waypoint mission start/stop command from app | The aircraft received a waypoint-mission go/stop command from the app (value 0). | high |
| `mis.route_end` | [L-MIS] | `^\[WP\]: stop mode\. end of trace, quit` | `[L-MIS][WP]: stop mode. end of trace, quit.` | Waypoint mission route completed | The waypoint mission reached the end of its route and the mission logic quit. | normal |
| `mis.p2p_timeout` | [L-MIS] | `^\{?\[WP_FLY_p2p\] p2p fly moving time out` | `[L-MIS]{[WP_FLY_p2p] p2p fly moving time out!` | Waypoint leg timed out | The point-to-point flight between waypoints exceeded its time limit. | normal |
| `mis.wp_detail` | [L-MIS] | `^(?:\[WP\]\|\[WP_INFO\]\|\[WP INFO\]\|\[SDK\]\|ack \|Ready to handle action\|I have done one\|finished all actions\|finish gimbal pitch\|received on/off\|input mission validity\|WP Mission: ack\|no ready waypoint mission\|req_watch_mode)` | `[L-MIS]I have done one action [20.20]` | Waypoint-mission processing step | Internal step of waypoint-mission execution (upload acknowledgements, per-waypoint actions, gimbal/camera handling, mission settings). | low |
| `osd.mission_finished` | [L-OSD] | `^Mission finished, now I tell app` | `[L-OSD]Mission finished, now I tell app:  3  size is 6` | Mission finished (reported to app) | The aircraft reported to the app that the mission finished. | normal |
| `osd.wp_finish_push` | [L-OSD] | `^push wp finish info to app` | `[L-OSD]push wp finish info to app success!` | Mission-finish info sent to app | The waypoint-mission finish information was pushed to the app. | low |
| `ioc.course_lock` | any | `^(?:Set CL\.\|\[IOC\.CL\s*\])\s*(?P<deg>-?[\d.]+) Degree` | `[L-IOC]Set CL. -171.7 Degree` | Course-lock heading recorded: -171.7 deg | Intelligent Orientation Control (IOC) recorded the course-lock reference heading of -171.7 degrees (the forward direction used by Course Lock mode). | normal |
| `ioc.reset` | any | `^old\.IOC reset` | `old.IOC reset intelligence_orientation_enabled` | Intelligent Orientation Control reset | Older-firmware message: the Intelligent Orientation Control setting was reset. | low |

## Home Point

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `home.first` | [L-HOME] | `^First\.\s*lati:\s*(?P<lat>-?\d+(?:\.\d+)?)\s+longti:\s*(?P<lon>-?\d+(?:\.\d+)?)\s+alti:\s*(?P<alt>-?\d+(?:\.\d+)?)` | `[L-HOME]First. lati:46.5214286 longti: 6.5673037 alti:355.61` | Home point first recorded | The flight controller recorded the home point (the return-to-home destination) for the first time in this power cycle at latitude 46.5214286, longitude 6.5673037. The altitude field (355.61 m) is the flight controller's altitude reference at that point; the log does not state its datum. | high |
| `home.update_rtk` | [L-HOME] | `^Update RTK\.\s*lati:\s*(?P<lat>-?\d+(?:\.\d+)?)\s+longti:\s*(?P<lon>-?\d+(?:\.\d+)?)\s+alti:\s*(?P<alt>-?\d+(?:\.\d+)?)(?:\s+alti_F:\s*(?P<alti_f>\d+))?(?:\s+curalt:\s*(?P<curalt>-?[\d.]+))?` | `[L-HOME]Update RTK. lati:46.5213889 longti: 6.5672863 alti:465.70 alti_F:2` | Home point updated (RTK position) | The home point was updated using the RTK (high-precision GNSS) position: latitude 46.5213889, longitude 6.5672863, altitude 465.70 m (RTK altitude reference). | high |
| `home.update` | [L-HOME] | `^Update\.\s*lati:\s*(?P<lat>-?\d+(?:\.\d+)?)\s+longti:\s*(?P<lon>-?\d+(?:\.\d+)?)\s+alti:\s*(?P<alt>-?\d+(?:\.\d+)?)(?:\s+curalt:\s*(?P<curalt>-?[\d.]+))?` | `[L-HOME]Update. lati:46.5214217 longti: 6.5673021 alti:355.46` | Home point updated | The flight controller updated the home point to latitude 46.5214217, longitude 6.5673021. DJI aircraft normally refresh the home point at take-off, and the pilot can reset it from the app or controller. The altitude field (355.46 m) is the flight controller's altitude reference at that point; the log does not state its datum. | high |
| `home.init_hp` | [L-HOME] | `^\[InitHP-(?P<n>\d+)\]:\s*lati:\s*(?P<lat>-?\d+(?:\.\d+)?)\s+longti:\s*(?P<lon>-?\d+(?:\.\d+)?)\s+alti:\s*(?P<alt>-?\d+(?:\.\d+)?)\((?P<q>\d+)\)\s*srcTp:(?P<src_type>\d+)\s*setTp:(?P<set_type>\d+)` | `[L-HOME][InitHP-2]: lati:40.0825682 longti:-88.2006590 alti:192.51(0) srcTp:2 setTp:2` | Home point initialised | The flight controller initialised the home point at latitude 40.0825682, longitude -88.2006590, altitude 192.51 m (source type 2, set type 2; these type codes are not publicly documented). | high |
| `home.upd_hp` | [L-HOME] | `^\[UpdHP\]:\s*lati:\s*(?P<lat>-?\d+(?:\.\d+)?)\s+longti:\s*(?P<lon>-?\d+(?:\.\d+)?)\s+alti:\s*(?P<alt>-?\d+(?:\.\d+)?)\((?P<q>\d+)\)\s*srcTp:(?P<src_type>\d+)\s*setTp:(?P<set_type>\d+)` | `[L-HOME][UpdHP]: lati:40.0825683 longti:-88.2006590 alti:192.51(1) srcTp:2 setTp:3 cnt` | Home point updated | The flight controller updated the home point to latitude 40.0825683, longitude -88.2006590, altitude 192.51 m (source type 2, set type 3; type codes not publicly documented). | high |
| `home.legacy_first` | any | `^(?P<lat>-?\d+(?:\.\d+)?)\s+(?P<lon>-?\d+(?:\.\d+)?)\s+(?P<alt>-?\d+(?:\.\d+)?)\s+First Home Point` | `41.2907681 -72.9621732 -23.95 First Home Point` | Home point first recorded | Older-firmware home-point line: the first home point of this power cycle was recorded at latitude 41.2907681, longitude -72.9621732 (altitude field -23.95 m, datum not stated). | high |
| `home.legacy` | any | `^(?P<lat>-?\d+(?:\.\d+)?)\s+(?P<lon>-?\d+(?:\.\d+)?)\s+(?P<alt>-?\d+(?:\.\d+)?)\s+Home Point` | `41.2907101 -72.9622394 -22.98 Home Point` | Home point recorded | Older-firmware home-point line: the home point was (re)recorded at latitude 41.2907101, longitude -72.9622394 (altitude field -22.98 m, datum not stated). | high |
| `home.takeoff_height_coord` | [L-HOME] | `^takeoff height coordinate changed \((?P<value>-?[\d.]+)\) based on:(?P<basis>\d+)` | `[L-HOME]takeoff height coordinate changed (10.072708) based on:2` | Take-off height reference changed | The take-off height reference was changed (value 10.072708, basis code 2; the basis codes are not publicly documented). | normal |
| `home.coord_rtk` | [L-HOME] | `^homepoint coor changed \((?P<dx>-?[\d.]+),(?P<dy>-?[\d.]+)\) based on RTK` | `[L-HOME]homepoint coor changed (10.271335,-42.824013) based on RTK:` | Home-point coordinates adjusted using RTK | The home-point coordinates were shifted based on the RTK position (offset values 10.271335, -42.824013 as logged; units not stated). | normal |

## Battery

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.low_battery` | any | `^Battery voltage warning level (?P<level>\d+) \(charge (?P<pct>\d+)%\)$` | `Battery voltage warning level 1 (charge 30%)` | Low-battery warning level 1 (30% charge) | The app log's battery warning level rose to 1 while the battery reported 30% charge. Level 1 is DJI's low-battery warning, level 2 the critical (serious low) warning. | high |
| `battery.state` | [L-BATTERY] | `^old:(?P<from>\w+)=+>+new:(?P<to>\w+)` | `[L-BATTERY]old:SAFE_FLY=>>>>new:GOHOME_CNTDOWN` | Battery protection state: SAFE_FLY -> GOHOME_CNTDOWN | The battery-protection state machine moved from SAFE_FLY (normal flight (no battery action)) to GOHOME_CNTDOWN (countdown before low-battery return-to-home). | high |
| `battery.countdown` | [L-BATTERY] | `^cntdown->(?P<count>\d+)` | `[L-BATTERY]cntdown->10` | Low-battery RTH countdown: 10 | Countdown before an automatic low-battery return-to-home (the DJI app shows a 10-second countdown the pilot can cancel). In the corpus it counts 10..1 at one-second intervals after the state change SAFE_FLY -> GOHOME_CNTDOWN and is followed by GOHOME. | high |
| `battery.alarm_level` | [L-BATTERY] | `^get alarm level (?P<level>\d+): (?P<pct>\d+)%, func:(?P<func>\d+)` | `[L-BATTERY]get alarm level 1: 30%, func:0` | Low-battery warning level 1 threshold: 30% | The low-battery warning level 1 threshold read from settings is 30% (action code 0). | normal |
| `battery.power_off_state` | [L-BATTERY] | `^power off\((?P<a>\d+)\)\s*-->\s*\((?P<b>[\d.]+)\)` | `[L-BATTERY]power off(3) --> (1.0)` | Battery power-off status message | Battery power-off status line; the numbers (3 -> 1.0) are internal codes that are not publicly documented. | low |
| `battery.comm_init` | [L-BATTERY] | `^-+comm_bat_init-+` | `[L-BATTERY]-------comm_bat_init-------` | Battery communication initialised | Start-up message: the flight controller initialised communication with the smart battery. | low |
| `battery.auth` | [L-BATTERY] | `^bat_auth_\w+?_(?P<what>init\|stop)` | `[L-BATTERY]bat_auth_origa_init` | Battery authentication routine init | Start-up message from the battery authentication routine (DJI checks that the battery is genuine). | low |
| `battery.rth_speeds` | [L-BATTERY] | `^(?:\[smart_battery\])?this fireware calc gohme speed:(?P<gohome_speed>[\d.]+) - land speed:(?P<land_speed>[\d.]+)` | `[L-BATTERY]this fireware calc gohme speed:7.800000 - land speed:1.500000` | Smart-RTH battery estimate speeds | The firmware uses a return speed of 7.800000 and a landing speed of 1.500000 (values consistent with m/s) when estimating the battery needed to return home and land. | low |
| `battery.time` | [L-BATTERY] | `^bat time (?P<epoch>\d+) \((?P<y>\d+) (?P<mo>\d+) (?P<d>\d+) (?P<h>\d+) (?P<mi>\d+) (?P<s>\d+)\)` | `[L-BATTERY]bat time 1618827888 (2021 4 19 10 24 48)` | Battery clock time: 2021-04-19 10:24:48 UTC | The smart battery's clock read 1618827888 seconds since 1970 (2021-04-19 10:24:48 UTC; the bracketed fields show the same date/time). | normal |
| `battery.rtc_ok` | [L-BATTERY] | `^bat rtc set ok` | `[L-BATTERY]bat rtc set ok` | Battery clock set | The battery's real-time clock was set successfully. | low |
| `battery.name` | [L-BATTERY] | `^Battery name\s*:(?P<name>.*)$` | `[L-BATTERY]Battery name    :ATL  NVT` | Battery cell/manufacturer name | Battery identification from the start-up banner: name field 'ATL  NVT'. | normal |
| `battery.design_cap` | [L-BATTERY] | `^Design cap\s*:\s*(?P<capacity_mah>\d+)` | `[L-BATTERY]Design cap      :5870` | Battery design capacity: 5870 mAh | The smart battery reports a design capacity of 5870 mAh. | normal |
| `battery.mfg_date` | [L-BATTERY] | `^manufacture Date\s*:\s*(?P<date>[\d/.-]+)` | `[L-BATTERY]manufacture Date:2018/11/15` | Battery manufacture date: 2018/11/15 | The smart battery reports manufacture date 2018/11/15. | normal |
| `battery.serial` | [L-BATTERY] | `^Serial number\s*:\s*(?P<serial>\S+)` | `[L-BATTERY]Serial number   :718` | Battery serial number: 718 | The smart battery reports serial number 718. Useful to tie a physical battery to this flight; DatCon reports this field as the battery serial. | normal |
| `battery.app_version` | [L-BATTERY] | `^bat app version:\((?P<version>[\d.]+)\)` | `[L-BATTERY]bat app version:(2.0.7.54)` | Battery firmware version 2.0.7.54 | The smart battery's firmware (application) version is 2.0.7.54. | low |
| `battery.cycles` | [L-BATTERY] | `^cycle count\s*:\s*(?P<cycles>\d+)` | `[L-BATTERY]cycle count    :8` | Battery charge-cycle count: 8 | The smart battery reports 8 charge cycles. | normal |
| `battery.ack_poweroff` | [L-BATTERY] | `^(?P<a>[\d.]+) \((?P<code>[0-9a-fx.]+)\) acked poweroff` | `[L-BATTERY]3.6 (0.b) acked poweroff` | Battery acknowledged power-off | The battery acknowledged a power-off request (internal fields 3.6 / 0.b). | low |
| `battery.barcode` | [L-BATTERY] | `^(?:\[BAT\])?(?:Battery )?barcode(?:\[\d+\])?\s*:\s*(?P<barcode>\S+)` | `[L-BATTERY]Battery barcode:0DQAFBG0320081` | Battery barcode: 0DQAFBG0320081 | The smart battery reports barcode 0DQAFBG0320081 (a battery identifier). | normal |
| `battery.upgrade_done` | [L-BATTERY] | `^finish battery upgrading` | `[L-BATTERY]finish battery upgrading` | Battery firmware update finished | The battery firmware update procedure finished. | normal |
| `battery.power_off_msg` | [L-BATTERY] | `^\[!\]get power off msg! motor:(?P<motor>\d+)` | `[L-BATTERY][!]get power off msg! motor:0` | Battery power-off request received | The flight controller received a power-off message from the battery (motor state flag 0; 0 = motors not running). | normal |
| `battery.legacy_power_off` | any | `^Battery power off` | `Battery power off` | Battery power off | Older-firmware line: the battery reported power off. | normal |
| `battery.legacy_smart_cfg` | any | `^\[BATTERY\]:reset default smart cfg - L1:(?P<l1>\d+)\s+L2:(?P<l2>\d+)` | `[BATTERY]:reset default smart cfg - L1:1  L2:2` | Smart-battery warning settings reset to defaults | The smart-battery warning configuration was reset to defaults (L1 code 1, L2 code 2). | low |
| `battery.legacy_info` | any | `^(?:Battery info:\|Manufacturer name\s*:\|Device name\s*:\|Device chemistry\s*:\|\[BAT\]read )(?P<value>.*)$` | `Device chemistry :LION` | Battery information field | Older-firmware battery information banner line (for example manufacturer, gauge chip or chemistry). | low |
| `battery.dev_sn` | [L-DEV] | `^(?P<dev>\d+)-BAT_LOG:\[bat\]sn:(?P<serial>\w+),(?P<code>0x[0-9a-fA-F]+)` | `[L-DEV]11-BAT_LOG:[bat]sn:6JYPL5KDA0037U,0x9030027` | Battery serial number: 6JYPL5KDA0037U | The battery log (device 11) reports battery serial number 6JYPL5KDA0037U (code 0x9030027). | normal |
| `battery.dev_auth` | [L-DEV] | `^(?P<dev>\d+)-(?:BAT_LOG:\[auth process\]\|G:\[auth process\]\|ss\]\|ocess\]\|tep\|\s*\d+ result\|ult)?:?\s*(?:step:\s*(?P<step>\d+)\s*)?result:\s*(?P<result>\d+) err: (?P<err>\d+)` | `[L-DEV]11-BAT_LOG:[auth process]: step: 4 result: 0 err: 0` | Battery authentication step result | Battery authentication log line from device 11 (step 4, result 0, error 0). Some of these lines are truncated fragments in the log. | low |
| `battery.api_gohome_status` | [L-API] | `^set battery go home status:(?P<status>\d+)` | `[L-API]set battery go home status:2` | Battery go-home status set (2) | The SDK/API interface set the battery go-home status to code 2 (codes not publicly documented). | normal |
| `version.battery_fw` | any | `^Bat\s+Ver\s*:\s*(?P<version>\S+)` | `[L-FMU/VERSION]Bat Ver :v255.255.255.255` | Battery firmware version field: v255.255.255.255 | Battery firmware version field from the flight controller's start-up banner. The value v255.255.255.255 means no version was reported at that moment (all bytes 0xFF). | low |
| `version.battery_sn` | any | `^Bat\s+SN\s*:\s*(?P<serial>.+?)\s*$` | `[L-FMU/VERSION]Bat SN  :0DQAFBG0320081` | Battery serial number(s): 0DQAFBG0320081 | Serial number(s) of the battery or batteries installed, from the flight controller's start-up banner. | normal |

## Gps

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.gps_drop` | any | `^GPS health dropped from (?P<from_level>\d+) to (?P<level>\d+) \((?P<sats>\d+) satellites\)$` | `GPS health dropped from 4 to 2 (7 satellites)` | GPS signal weakened (level 4 -> 2) | The app log's GPS signal level (0-5 bars) dropped from 4 to 2 with 7 satellites in use. | high |
| `txt.gps_recovered` | any | `^GPS health recovered to (?P<level>\d+) \((?P<sats>\d+) satellites\)$` | `GPS health recovered to 4 (12 satellites)` | GPS signal recovered (level 4) | The app log's GPS signal level (0-5 bars) recovered to 4 with 12 satellites in use. | high |
| `txt.gps_lost` | any | `^GPS positioning no longer used \(cause: (?P<cause>[^)]*)\)$` | `GPS positioning no longer used (cause: GpsNumNonEnough)` | GPS positioning lost | The app log shows the aircraft stopped using GPS for positioning (too few satellites). Without GPS the aircraft cannot hold position and drifts with the wind (ATTI behaviour). | high |
| `fdi.rtk_link` | [L-FDI] | `^RTK\((?P<idx>\d+)\):\s*(?P<what>disconnect\|reconnect)` | `[L-FDI]RTK(0): disconnect` | RTK module disconnect | Fault detection reports the RTK (high-precision GNSS) module #0 disconnected. | normal |
| `fdi.rtk_jump` | [L-FDI] | `^rtk:jump_hp=(?P<jump_h>-?[\d.]+), hv_mod=(?P<hv_mod>-?[\d.]+), jump_z=(?P<jump_z>-?[\d.]+)` | `[L-FDI]rtk:jump_hp=44.778339, hv_mod=0.024783, jump_z=2.695053` | RTK position jump measured | Fault detection measured a jump in the RTK position: horizontal 44.778339, vertical 2.695053 (units not stated; consistent with metres). | normal |
| `gps.signature_ok` | any | `^(?:<GPS INFO>)?GPS ecdsa_verify OK` | `[L-GPS]<GPS INFO>GPS ecdsa_verify OK` | GPS module signature verified | The cryptographic (ECDSA) signature check on the GPS module passed. | low |
| `gps.frequency_error` | [L-GPS] | `^<GPS ERROR>.*frequency error` | `[L-GPS]<GPS ERROR>[0][0x08] frequency error:lost msg` | GPS message-rate error | The GPS driver reported that GPS messages did not arrive at the expected rate (missing/lost messages). | normal |
| `gps.init_no_ack` | [L-GPS] | `^<GPS ERROR>.*INIT\[(?P<step>\d+)\] ERROR \[(?P<msg>[A-Z0-9-]+)\] do not receive ack` | `[L-GPS]<GPS ERROR>GPS[1][0] INIT[19] ERROR [MON-HW] do not receive ack` | GPS configuration step not acknowledged (MON-HW) | During initialisation the GPS receiver did not acknowledge configuration step 19 (MON-HW). | normal |
| `gps.agps_incomplete` | [L-GPS] | `^<AGPS ERROR>agps transmit is not finish` | `[L-GPS]<AGPS ERROR>agps transmit is not finish or encounter error!` | Assisted-GPS data transfer incomplete | Transfer of assisted-GPS (AGPS) data from the app to the aircraft was not finished or failed. | normal |
| `gps.agps_info` | [L-GPS] | `^<AGPS INFO>` | `[L-GPS]<AGPS INFO>motor start,agps comm will stop...` | Assisted-GPS (AGPS) status | Status message about assisted-GPS data exchanged with the app (AGPS speeds up the first satellite fix). | low |
| `gps.init_info` | any | `^(?:<GPS INFO>\|drv_gps_init\|gps init: )` | `[L-GPS]<GPS INFO>drv_gps_init` | GPS receiver start-up message | Start-up/initialisation message from the GPS receiver driver (baud rate, firmware status, receiver ID, acknowledgements). | low |
| `mis.rtk_switch` | [L-MIS] | `^\s*RTK is (?P<state>ok\|not ready), switch to (?P<target>\w+)` | `[L-MIS] RTK is ok, switch to rtk` | Mission positioning switched to rtk | The mission module switched its positioning source to rtk because RTK is ok. | normal |
| `mis.gps2rtk` | [L-MIS] | `^gps2rtk offset\s+(?P<x>-?[\d.]+)\s+(?P<y>-?[\d.]+)\s+(?P<z>-?[\d.]+)` | `[L-MIS]gps2rtk offset -10.04  42.95  -10.09` | GPS-to-RTK position offset | Offset between the GPS and RTK positions: -10.04, 42.95, -10.09 (units not stated; consistent with metres). | low |
| `rtk.station` | any | `^\[rtk\]receive MC set motive station` | `[rtk]receive MC set motive station` | RTK: flight controller set mobile-station mode | The RTK module received the flight controller's 'set motive (mobile) station' command. | low |

## Sensors

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `fdi.ns_fault` | [L-FDI] | `^NS\((?P<ns>\d+)\)\s+(?P<src>[A-Z]+)\((?P<idx>\d+)\):\s*fault (?P<onoff>on\|off)\s*,\s*(?P<reason>\w+)` | `[L-FDI]NS(0) RTK(0): fault on , conformity` | Navigation fault raised: RTK GNSS (conformity) | Fault detection raised a fault on RTK GNSS #0 feeding navigation system 0: its data disagreed with the other navigation sources. | normal |
| `fdi.sensor_fault` | [L-FDI] | `^(?P<src>[A-Z]+)\((?P<idx>\d+)\):\s*fault (?P<onoff>on\|off)\s*,\s*(?P<reason>\w+)` | `[L-FDI]ULTRASONIC(0): fault on , disconnect` | Sensor fault raised: ultrasonic height sensor (disconnect) | Fault detection raised a fault on ultrasonic height sensor #0: no data was received (disconnected). Raise/clear pairs at power-on are common while sensors start. | normal |
| `fdi.baro_cfg` | [L-FDI] | `^barometer_pool` | `[L-FDI]barometer_pool[0].priv.config.filter_config_flag:(541263214)` | Barometer fault-detection configuration | Start-up message: barometer filter configuration used by fault detection. | low |
| `sensor.baro_init` | [L-SENSOR] | `^\[baro\((?P<idx>\d+)\)\]\s*std_filtered_air_press init succeed: press:(?P<pressure>[\d.]+)` | `[L-SENSOR][baro(0)] std_filtered_air_press init succeed: press:97.3` | Barometer initialised (air pressure 97.3) | Barometer 0 initialised with an air pressure of 97.3 (consistent with kPa). | low |
| `sensor.startup` | [L-SENSOR] | `^(?:\[ASR\]\|imu group\|compass group\|baro group\|\[baro\(\d+\)\]\|\s*hw_ver voltage)` | `[L-SENSOR]imu group0 ok` | Sensor start-up / synchronisation message | Start-up message from the sensor hub (sensor groups, barometer, sensor time synchronisation 'ASR'). | low |
| `fmu.busy_device_changed` | [L-FMU/DM] | `^Busy Device Changed\. Type:(?P<type_name>\w+), <ID:(?P<from_id>\d+) idx:(?P<from_idx>\d+)-->ID:(?P<to_id>\d+) idx:(?P<to_idx>\d+)>` | `[L-FMU/DM]Busy Device Changed. Type:imu1_ext_ns, <ID:9 idx:0-->ID:20 idx:1>, change ti` | Active imu1_ext_ns device switched | The device manager switched the active 'imu1_ext_ns' device from id 9 (index 0) to id 20 (index 1), e.g. a switch to a redundant sensor. | normal |
| `radar.filter` | [L-RADAR] | `^Set radar filter` | `[L-RADAR]Set radar filter wide to 20.00` | Radar filter setting | Setting of the obstacle/terrain radar's filter. | low |

## Compass

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `compass.cali_location` | [L-COMPASS] | `^\[read_mag_cali_pt\]lat:\s*(?P<lat_rad>-?[\d.]+),\s*lon:\s*(?P<lon_rad>-?[\d.]+)` | `[L-COMPASS][read_mag_cali_pt]lat:0.81195, lon:0.11462` | Last compass-calibration location | Stored location of the most recent compass calibration, logged in radians (0.81195, 0.11462), i.e. about latitude 46.521308, longitude 6.567242. Five decimals in radians limit precision to roughly +/-30 m. | high |
| `compass.cali_date` | [L-COMPASS] | `^\[read_mag_cali_pt\]height:\s*(?P<height>-?[\d.]+),\s*date:\s*(?P<date>\d{8})` | `[L-COMPASS][read_mag_cali_pt]height:404.0, date:20210322` | Last compass calibration: 2021-03-22 | The most recent compass calibration is stored as dated 2021-03-22, at height value 404.0 (as logged). | high |
| `compass.cali_invalid` | [L-COMPASS] | `^\[(?:cur\|frame) cali data\].*invalid` | `[L-COMPASS][cur cali data]fmu:0,user:0,current cali data invalid` | Compass calibration record not valid | The compass module reports that one of its stored calibration records is invalid; at start-up it then loads calibration from another store (see neighbouring 'read app cali data' lines). | low |
| `compass.cali_load` | [L-COMPASS] | `^\[read (?:app cali\|load) data\]` | `[L-COMPASS][read app cali data]fmu:0,user:0,bias:[-315.975 -17.736 -14.316]` | Compass calibration data loaded | Start-up message: the compass module loaded stored calibration values (scale/bias or record metadata). | low |
| `compass.config` | [L-COMPASS] | `^(?:enable:\(\d\),w_fstop\|sync_topology_verify_config)` | `[L-COMPASS]enable:(1),w_fstop(10.000000),w_fs(150.000000),auto_cali(1)` | Compass module configuration | Start-up message describing the compass module configuration. | low |
| `compass.legacy_cali_init` | any | `^compass calibration init!` | `compass calibration init!` | Compass calibration module initialised | Start-up message: compass calibration module initialised (this does not mean a calibration was performed). | low |
| `compass.chip` | any | `^(?:IST83\w+\|ist83xx)\b.*` | `IST8303[2:0x18]:Init...` | Compass sensor chip message | Message from the magnetometer (compass) chip driver (IST83xx series). | low |

## Imu

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `fdi.tilt_motor_on` | [L-FDI] | `^tilt when motor on = (?P<tilt>-?[\d.]+), static flag = (?P<static>\d)` | `[L-FDI]tilt when motor on = 0.021897, static flag = 1` | Tilt at motor start: 0.021897 | Fault detection recorded the aircraft's tilt when the motors started (0.021897; unit not stated, value consistent with radians) and a static flag of 1. | normal |
| `fdi.imu_ok` | [L-FDI] | `^\[IMU\](?P<idx>\d+)\s+(?P<check>dir\|mis cali\|level 1&2 temp cali\|base cali\|cali ver) ok` | `[L-FDI][IMU]0 cali ver ok 4 4 9 9` | IMU 0 check passed: cali ver | Start-up self-check: IMU 0 passed the 'cali ver' check (orientation / misalignment / temperature / base calibration / calibration version). | low |
| `fdi.imu_err_status` | [L-FDI] | `^\[IMU\]\s*gyro acc (?P<idx>\d+) err status (?P<code>\w+)` | `[L-FDI][IMU] gyro acc 0 err status 4040` | IMU 0 error status 4040 | Fault detection reports gyro/accelerometer 0 error status code 4040 (bit field; meaning not publicly documented). | normal |
| `imu.gyro_acc_cfg` | [L-GYRO_ACC] | `^(?:turn on temp ctrl\|gyr_acc_config\|fitler\|fz:\|\s*As=\|vib detect\|\[mark\]\|\[shadow\]\|\[\d+\]\|imu_\d+ group)` | `[L-GYRO_ACC]turn on temp ctrl(0), cur_temp:0.0, reason:gyro_acc_tmp_ctl_init` | IMU (gyro/accelerometer) start-up configuration | Start-up message from the gyroscope/accelerometer driver (filters, temperature control, calibration and registration). | low |
| `imu.heading_state` | [L-IMU] | `^\[heading\] state: (?P<state>\w+)` | `[L-IMU][heading] state: fixed` | Heading estimate state: fixed | The IMU heading estimator reported state 'fixed'. | normal |
| `imu.switch_level` | [L-IMU] | `^set imu switch level = (?P<level>\d+)` | `[L-IMU]set imu switch level = 1` | IMU switch level set to 1 | The IMU redundancy/switch level was set to 1. | normal |
| `imu.legacy_cali` | any | `^(?:app )?temp cali\|^mis cali\|^imu_type:\|^imu status:` | `temp cali (-11.000000,55.005882) aa fw:4 4` | IMU calibration/status data | Older-firmware start-up line with IMU temperature/misalignment calibration data or status. | low |

## Radio Link

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.rc_signal_drop` | any | `^RC (?P<name>\w+) signal dropped to 0% \(was (?P<was>\d+)%\)$` | `RC uplink signal dropped to 0% (was 80%)` | Remote-controller uplink signal lost | The app log's remote-controller uplink signal strength fell to 0% (previously 80%). | high |
| `txt.rc_signal_back` | any | `^RC (?P<name>\w+) signal restored to (?P<pct>\d+)%$` | `RC uplink signal restored to 75%` | Remote-controller uplink signal restored | The app log's remote-controller uplink signal strength came back to 75%. | high |
| `txt.rc_lost` | any | `^OSD reports RC link lost \(rc_outcontrol\)$` | `OSD reports RC link lost (rc_outcontrol)` | Remote-controller link lost | The aircraft's OSD record set its 'RC out of control' flag: the aircraft was no longer receiving the remote controller. | high |
| `txt.rc_back` | any | `^OSD reports RC link back \(rc_outcontrol cleared\)$` | `OSD reports RC link back (rc_outcontrol cleared)` | Remote-controller link restored | The aircraft's OSD 'RC out of control' flag cleared: the remote-controller link was restored. | high |
| `rc.raw_out_of_range` | [L-RC] | `^\[FATAL\]rc raw data not in range` | `[L-RC][FATAL]rc raw data not in range?` | Remote-control input data out of range | The flight controller flagged raw remote-control channel data as outside the expected range. Despite the 'FATAL' label this line repeats many times in ordinary flights in the corpus; on its own it does not show a loss of control. | normal |
| `rc.lock_cmd` | [L-RC] | `^set rc lock cmd!` | `[L-RC]set rc lock cmd!` | Remote-controller lock command | The flight controller applied a remote-controller 'lock' command; in the corpus this accompanies waypoint-mission camera actions (see '[L-MIS][WP] rc lock param'). | low |
| `rc.lock_cmd_ok` | [L-RC] | `^set rc lock cmd (?P<value>\d+) success!` | `[L-RC]set rc lock cmd 0 success!` | Remote-controller lock command applied | The remote-controller lock command (value 0) was applied successfully. | low |
| `rc.connect` | [L-RC] | `^rc_connect:(?P<from>\d)=>(?P<to>\d)` | `[L-RC]rc_connect:0=>1` | Remote-controller link connected | The flight controller's remote-controller connection flag changed from 0 to 1: the controller link is up. | high |
| `rc.resume` | [L-RC] | `^rc_resume @ \[(?P<ms>\d+) ms\]` | `[L-RC]rc_resume @ [124130 ms]` | Remote-controller input resumed | Remote-controller input became active again at 124.1 s on the flight-controller clock. In the corpus this appears once per power cycle when the controller link is first established; it is also logged after an interruption. | high |
| `rc.resume_lock` | [L-RC] | `^(?P<op>set\|clr) rc_resume_lock` | `[L-RC]set rc_resume_lock` | Remote-controller resume lock set | Internal flag guarding remote-controller resume handling was set or cleared ('set'). | low |
| `rc.cmd` | [L-RC] | `^rc cmd:(?P<cmd>\w+)` | `[L-RC]rc cmd:STD_GO_HOME` | Remote-controller command: return-to-home | The remote controller sent command 'STD_GO_HOME' (return-to-home) to the flight controller. | high |
| `rc.func_disable` | [L-RC] | `^rc_func_disable @ \[(?P<ms>\d+) ms\]` | `[L-RC]rc_func_disable @ [863317 ms]` | Remote-controller functions disabled | Remote-controller functions were disabled at 863.3 s on the flight-controller clock. | normal |
| `sys.app_lost` | [L-SYS] | `^app lost connection!` | `[L-SYS]app lost connection!` | Mobile app connection lost | The flight controller lost its connection to the mobile app (the app link via the remote controller or Wi-Fi). | high |
| `sys.app_connected` | [L-SYS] | `^app connected!` | `[L-SYS]app connected!` | Mobile app connected | The flight controller established a connection with the mobile app. | normal |
| `boot.ofdm_failed` | any | `^\[link_ofdm\] setup entry failed` | `{root}INFO:[link_ofdm] setup entry failed` | Radio (OFDM) link setup failed | Setting up the OFDM radio-link entry failed (OFDM is the video/control radio link to the remote controller). | normal |
| `boot.ofdm_tx_err` | any | `^\[Err\] OFDM_TX State:(?P<state>0x\w+)` | `[Err] OFDM_TX State:0x0000000c` | Radio (OFDM) transmitter error state 0x0000000c | The OFDM radio transmitter reported error state 0x0000000c. | normal |
| `senddata.msg` | [L-SEND DATA] | `^\[(?:INFO\|ERROR)\]` | `[L-SEND DATA][INFO] [Reset] Dev_id: 0` | Telemetry push configuration message | Message from the module that pushes telemetry data packages ('bags') to the app/SDK. | low |

## Motors

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.motor_start` | any (record kind: motor_start) | `^Motors started$` | `Motors started` | Motors started | DroneDog derived this from the app log: the 'motors on' flag changed from off to on. | high |
| `txt.motor_running_at_start` | any (record kind: motor_start) | `^Motors already running at log start$` | `Motors already running at log start` | Motors already running when log began | The first record of the app log shows the motors already running, so the motor start is not in this log. | high |
| `txt.motor_stop` | any (record kind: motor_stop) | `^Motors stopped$` | `Motors stopped` | Motors stopped | DroneDog derived this from the app log: the 'motors on' flag changed from on to off. | high |
| `txt.motor_start_blocked` | any | `^Motor start blocked: (?P<cause>[\w()]+)$` | `Motor start blocked: CompassError` | Motor start blocked: CompassError | The aircraft refused to start its motors; the app log gives the reason 'CompassError' (compass error). | high |
| `motor.start` | [L-FMU/MOTOR] | `^Start\.\s*reason:\s*(?P<reason>\S+)` | `[L-FMU/MOTOR]Start. reason:ACT.Takeoff` | Motors started (automatic take-off command) | The flight controller started the motors; reason code 'ACT.Takeoff' (automatic take-off command). DatCon uses this line as the motor-start time. | high |
| `motor.stop` | [L-FMU/MOTOR] | `^Stop\.\s*reason:\s*(?P<reason>\S+)` | `[L-FMU/MOTOR] Stop. reason:ACT.landing` | Motors stopped (landing completed) | The flight controller stopped the motors; reason code 'ACT.landing' (landing completed). DatCon uses this line as the motor-stop time. | high |
| `motor.legacy_start` | any | `^\[M\.\s*Start\]\s*(?P<reason>\S+)` | `[M.Start]ACT.Takeoff` | Motors started (automatic take-off command) | Older-firmware motor-start line; reason code 'ACT.Takeoff' (automatic take-off command). | high |
| `motor.legacy_stop` | any | `^\[M\.\s*Stop\]\s*(?P<reason>\S+)` | `[M. Stop]landing.RC_Thr` | Motors stopped (landed with the throttle stick held down) | Older-firmware motor-stop line; reason code 'landing.RC_Thr' (landed with the throttle stick held down). | high |
| `motor.total` | [L-FMU/MOTOR] | `^Total start times:\s*(?P<count>\d+),\s*time:\s*(?P<seconds>[\d.]+)` | `[L-FMU/MOTOR]Total start times: 1, time: 357.78` | Motor run #1 ended (357.78 s total motor time) | Logged when the motors stop: this was motor start number 1 since power-on, and the motors have run for 357.78 s in total since power-on (in the corpus the value equals the summed start-to-stop intervals). | normal |
| `motor.legacy_total` | any | `^Motor Start\s+(?P<count>\d+)\s+Total\s+(?P<total>[\d.]+)` | `Motor Start  3 Total  793.84` | Motor start #3 | Older-firmware motor counter line: motor start number 3; the 'Total' field is 793.84 (unlabelled in the log). | normal |
| `motor.safe_near_ground` | [L-FMU/MOTOR] | `^safe_near_grd:(?P<state>true\|false)` | `[L-FMU/MOTOR]safe_near_grd:true` | Near-ground safety flag: true | Internal motor-safety flag indicating whether the aircraft is considered safely near the ground (true). It toggles around take-off and landing. | low |
| `motor.esc_disconnect` | [L-FMU/MOTOR] | `^fault (?P<onoff>on\|off)\s*,\s*esc\((?P<esc>\d+)\) is disconnect` | `[L-FMU/MOTOR]fault on , esc(1) is disconnect` | ESC 1 disconnect fault raised | The motor manager raised a fault for electronic speed controller (ESC) 1 not communicating. In the corpus a raise/clear pair is common during power-on while ESCs come up. | normal |
| `motor.force_disable_flag` | [L-FMU/MOTOR] | `^read motor force disable flag successfully` | `[L-FMU/MOTOR]read motor force disable flag successfully!` | Motor force-disable flag read | Start-up message: the stored motor force-disable flag was read successfully. | low |
| `motor.esc_err` | [L-FMU/MOTOR] | `^esc err, status:(?P<status>\w+)` | `[L-FMU/MOTOR]esc err, status:0` | ESC error status 0 | The motor manager reported ESC error status code 0 (0 means no error bits set). | low |
| `esc.link_up` | any | `^ESC(?P<esc>\d+) link up` | `[L-ESC]ESC0 link up` | ESC 0 link established | Communication with electronic speed controller (ESC) 0 was established. | low |
| `esc.protocol_error` | [L-ESC] | `^ESC(?P<esc>\d+) protocol error - (?P<code>0x\w+)` | `[L-ESC]ESC0 protocol error - 0x12!` | ESC 0 protocol error 0x12 | The flight controller reported a communication protocol error with ESC 0 (code 0x12). | normal |
| `esc.info` | any | `^(?:esc alive info\|escm ppm test\|ESC\d+ version:\|Loader\s*=\|Firmware\s*=)` | `[L-ESC]esc alive info = 0x0` | ESC start-up information | Start-up information about the electronic speed controllers (alive mask, self-test, versions). | low |
| `esc.status_changed` | any | `^\[esc_is_(?P<what>stall\|empty)\] status changed: last\((?P<last>0x\w+)\) != current\((?P<cur>0x\w+)\)` | `[esc_is_stall] status changed: last(0xffffffff) != current(0x00000000)` | ESC stall status changed | The ESC 'stall' status bit mask changed from 0xffffffff to 0x00000000 (0x00000000 = no ESC flagged). | normal |
| `pwm.actuator_mode` | [L-PWM] | `^set actuator mode:(?P<mode>\w+)` | `[L-PWM]set actuator mode:CTRL` | Motor outputs active (actuator mode CTRL) | The motor output driver switched to active control: motor commands are now being sent (accompanies motor start). | normal |
| `pwm.cfg` | [L-PWM] | `^(?:actuator_cfg_changed\|f_chl_cfg changed)` | `[L-PWM]actuator_cfg_changed![ACTUATOR_0]` | Motor-output configuration loaded | Start-up message: motor/actuator output configuration changed (loaded). | low |

## Camera Gimbal

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `mis.simple_shot` | [L-MIS] | `^\[WP\] send simple shot` | `[L-MIS][WP] send simple shot` | Waypoint action: take photo | The waypoint mission commanded the camera to take a single photo. | normal |
| `camera.shot_position` | [L-CAMERA] | `^cam success\((?P<h>\d+) (?P<m>\d+) (?P<s>\d+) (?P<n>\d+)\)\((?P<lon>-?[\d.]+),(?P<lat>-?[\d.]+),(?P<alt>-?[\d.]+)\)` | `[L-CAMERA]cam success(9 36 30 777015902)(6.567274,46.521394,467.845540) (0.009494,0.01` | Photo taken at 46.521394, 6.567274 | The camera reported a successful capture. Position logged with it: latitude 46.521394, longitude 6.567274 (the log writes longitude first), altitude 467.845540 (datum not stated). The first group (9 36 30 777015902) reads as hours, minutes, seconds and an unlabelled counter. | normal |
| `camera.set_date` | [L-CAMERA] | `^cmd_handler_setting_date (?P<date>[\d-]+),\s*(?P<time>[\d:]+)` | `[L-CAMERA]cmd_handler_setting_date 2023-09-22, 15:33:21` | Camera clock set to 2023-09-22 15:33:21 | The flight controller sent date/time 2023-09-22 15:33:21 to the camera (camera clock synchronisation; time zone not stated). | normal |
| `camera.set_time_failed` | [L-CAMERA] | `^setting time\s+failed ret:(?P<ret>-?\d+)` | `[L-CAMERA]setting time  failed ret:-17` | Camera clock setting failed | Setting the camera's clock failed (return code -17). | normal |
| `camera.shot_ok` | [L-CAMERA] | `^SUCCESSED SIMPLE_SHOT` | `[L-CAMERA]SUCCESSED SIMPLE_SHOT` | Photo captured (single shot) | The camera confirmed a single-photo capture. | normal |
| `camera.shot_cmd` | [L-CAMERA] | `^Send camera shot` | `[L-CAMERA]Send camera shot, timer 0.005000` | Photo command sent to camera | The flight controller sent a take-photo command to the camera. | low |
| `camera.handshake` | [L-CAMERA] | `^(?:Receive ack\|Receive reply\|<\d+>\|SIMPLE_SHOT, wait\|recv ack\|recv shot ack\|WP session\|<SET_MODE>)` | `[L-CAMERA]Receive ack result 0` | Camera command handshake | Acknowledgement/handshake step in flight-controller-to-camera communication (mode changes, shot acknowledgements, sessions). | low |
| `gimbal.disconnect` | [L-GIMBAL] | `^The (?P<which>\w+) gimbal (?P<idx>\d+) disconnect` | `[L-GIMBAL]The 1st gimbal 0 disconnect` | Gimbal disconnected | The flight controller reports the 1st gimbal (#0) disconnected. | normal |

## Safety

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.osd_flag` | any | `^OSD flag set: (?P<flag>\w+)$` | `OSD flag set: not_enough_force` | Not enough thrust | The aircraft reported it could not produce enough thrust (DJI app: 'not enough force'), e.g. damaged propeller or motor, overload, or weak battery. (App-log OSD flag 'not_enough_force' became set.) | high |
| `txt.app_warning` | any (record kind: app_warning) | `^(?P<message>.+)$` | `Strong wind. Fly with caution` | App warning: Strong wind. Fly with caution | Warning the DJI flight app displayed to the pilot, recorded verbatim (it may be in the app's display language). | normal |
| `txt.app_serious_warning` | any (record kind: app_serious_warning) | `^(?P<message>.+)$` | `Motor overloaded` | App serious warning: Motor overloaded | Serious warning the DJI flight app displayed to the pilot, recorded verbatim (it may be in the app's display language). | high |
| `fdi.ctrl_event` | any | `^\[FDI CTRL\] event:\s*(?P<det>.*?)\s*turn on` | `[L-FDI][FDI CTRL] event: impact turn on` | Fault detector enabled: impact | A flight-controller fault-detection (FDI) monitor named 'impact' was switched on. This start-up message records that the detector is active; it does NOT mean the condition (for example an impact or fly-away) occurred. | low |
| `fdi.legacy_module` | any | `^\[FDI (?P<sensor>[A-Z]+)\[(?P<idx>\d+)\]\]\s*(?:event)?:?\s*(?P<what>.+)$` | `[FDI GPS[1]] event:turn on` | Fault detection (GPS): turn on | Older-firmware fault-detection start-up message for the GPS monitor #1: 'turn on'. | low |
| `geo.height_status` | [L-GEO] | `^\[(?P<tick>[\d.]+)\]:height:(?P<height>\d+),pi:(?P<pi>\d+),pih:(?P<pih>\d+),pa:(?P<pa>\d+),pab:(?P<pab>\d+),isd:(?P<isd>\d+)` | `[L-GEO][70036736.0]:height:500,pi:1,pih:1,pa:0,pab:0,isd:1` | Fly-safe (GEO) height status: 500 | Periodic GEO/fly-safe status line. The 'height' field (500) is consistent with the applicable altitude limit in metres; the other flags (pi, pih, pa, pab, isd) are not publicly documented. | low |
| `geo.nfz_db` | [L-GEO] | `^\[TZ\]nfz_db num:(?P<count>\d+), no_valid_pos:(?P<no_valid_pos>\d+)` | `[L-GEO][TZ]nfz_db num:3, no_valid_pos:0` | No-fly-zone database entries: 3 | The fly-safe module reports 3 no-fly-zone database entries in use (no-valid-position flag 0). | normal |
| `geo.app_db_entry` | [L-GEO] | `^\[print_app_db\]\[(?P<slot>\d+)\] id:(?P<id>\d+) \((?P<lat_e6>-?\d+), (?P<lon_e6>-?\d+)\) (?P<value>\d+)` | `[L-GEO][print_app_db][0] id:0 (46545278, 6616667) 2414` | App fly-safe zone entry at 46.545278, 6.616667 | Fly-safe zone data supplied by the app: slot 0, id 0, centre latitude 46.545278, longitude 6.616667 (stored as degrees x 1,000,000); the trailing value 2414 is unlabelled. | normal |
| `geo.app_db_update` | [L-GEO] | `^\[(?:fmu_api_handler_update_app_db\|update_nfz_app_db)\]` | `[L-GEO][update_nfz_app_db]update app db[1]` | Fly-safe database update from app | Step in transferring fly-safe (no-fly-zone) data from the app to the aircraft. | low |
| `geo.lost_app` | [L-GEO] | `^lost app, clear app_pos_info` | `[L-GEO]lost app, clear app_pos_info` | Fly-safe: app lost, app position cleared | The fly-safe module lost contact with the app and cleared the app-supplied position information. | normal |
| `geo.user_id` | [L-GEO] | `^\[update_user_id\]user id updated (?P<old>\d+)=>(?P<new>\d+)` | `[L-GEO][update_user_id]user id updated 4559911=>0` | Fly-safe user ID changed | The fly-safe module's stored user ID changed from 4559911 to 0. | normal |
| `geo.flight_connect` | [L-GEO] | `^\[nfz_flight_monitor\]flight connect changed! (?P<from>\d+) -> (?P<to>\d+)` | `[L-GEO][nfz_flight_monitor]flight connect changed! 0 -> 1` | Fly-safe monitor connection 0 -> 1 | The fly-safe flight monitor's connection state changed from 0 to 1. | low |
| `geo.relation` | [L-GEO] | `^\[record_flysafe_info\]drone relation (?P<from>\w+) => (?P<to>\w+)` | `[L-GEO][record_flysafe_info]drone relation OUT_NFZ => NFZ_UNKNOWN` | No-fly-zone relation: OUT_NFZ -> NFZ_UNKNOWN | The fly-safe module's assessment of the aircraft's relation to no-fly zones changed from OUT_NFZ (outside any no-fly zone) to NFZ_UNKNOWN (relation unknown (e.g. no valid position yet)). | normal |
| `geo.internal` | [L-GEO] | `^\[(?:update_fc_db_handle\|sa_drone_storage_process)\]` | `[L-GEO][update_fc_db_handle]update fc db num:0` | Fly-safe database housekeeping | Internal fly-safe database update or storage message. | low |
| `geo.country_pos` | any | `^\[country_limit_process\]cur pos:\((?P<lat>-?\d+(?:\.\d+)?) (?P<lon>-?\d+(?:\.\d+)?)\)` | `[L-DBG2][country_limit_process]cur pos:(46.52 6.57) (0.81,0.11)` | Country flight-restriction check position | The country flight-restriction check used the position latitude 46.52, longitude 6.57 (rounded to 2 decimals in the log). | normal |
| `geo.country_result` | any | `^\[country_limit_process\]region:(?P<region>\w+) strategy:(?P<strategy>\d+) result:(?P<result>\w+)` | `[L-DBG2][country_limit_process]region:REGION_ALL strategy:250 result:CAN_FLY` | Country flight-restriction result: CAN_FLY | The country flight-restriction check for region REGION_ALL (strategy 250) returned 'CAN_FLY'. | normal |
| `geo.country_init` | any | `^\[country_limit_result_init\]` | `[L-DBG2][country_limit_result_init][INIT]strategy:250 can fly area num:1` | Country flight-restriction check initialised | Start-up message from the country flight-restriction module. | low |
| `geo.airport_limit` | any | `^airport limit (?P<what>inited\|data need update)` | `airport limit inited[1]` | Airport no-fly data inited | Older-firmware fly-safe message about the built-in airport no-fly-zone data ('inited'). | normal |
| `geo.whitelist` | any | `^\[whitelist\]` | `[whitelist]invalid flash key` | Fly-safe unlock (whitelist) message | Message from the fly-safe unlock/whitelist module. | low |
| `cfg.rc_lost_action` | [L-CFG] | `^(?P<op>get\|set) rc lost act\.\s*(?:cmd:)?\s*(?P<act>\d+)` | `[L-CFG]get rc lost act. 2` | RC signal-lost action read back as: return-to-home | The failsafe action for remote-controller signal loss was read back as code 2 (return-to-home; numbering as used by the DJI Mobile SDK: 0 hover, 1 land, 2 return-to-home). | normal |

## System

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `version.fc_serial` | any | `^Mc\s+ID\s*:\s*(?P<serial>\S+)` | `[L-FMU/VERSION]Mc  ID  :0UYKFBE00200NV` | Flight-controller serial number: 0UYKFBE00200NV | Serial number of the flight controller ('Mc ID'), identifying the aircraft's main controller. DatCon reports this field as the aircraft's mcID(SN). | normal |
| `version.fc_firmware` | any | `^Mc\s+Ver\s*:\s*(?P<version>\S+)` | `[L-FMU/VERSION]Mc  Ver :v3.2.44.7` | Flight-controller firmware v3.2.44.7 | Firmware version of the flight controller ('Mc Ver'). | normal |
| `version.svn` | any | `^svn\s+Ver\s*:\s*(?P<svn>\S+)` | `[L-FMU/VERSION]svn Ver :ff21b233464aeeb77c0a8ddf7ee22122826284eb` | Firmware source revision ff21b233464aeeb77c0a8ddf7ee22122... | Source-control revision identifier of the flight-controller firmware build. | low |
| `version.build_time` | any | `^Time\s*:\s*(?:commit:(?P<commit>[\d-]+ [\d:]+)\s*/build:(?P<build>[\d-]+ [\d:]+)\|(?P<when>.+))` | `[L-FMU/VERSION]Time    :commit:2017-11-15 22:11:15 /build:2017-11-16 10:29:44` | Firmware build time | Build/commit time of the flight-controller firmware (this is when the firmware was compiled, not the flight time). | low |
| `version.release` | any | `^FC - RELEASE FIRMWARE` | `[L-FMU/VERSION]FC - RELEASE FIRMWARE` | Release (production) firmware | The flight controller reports that it is running a release (production) firmware build. | low |
| `version.startup` | any | `^startup:(?P<seconds>[\d.]+)` | `<FLY>startup:3.116482` | Flight-controller start-up (3.116482) | Start-up banner line from older firmware; the value 3.116482 is an unlabelled start-up time figure. | low |
| `version.board` | any | `^Board:"(?P<board>[^"]+)"` | `<FLY>Board:"ag405rtk"` | Flight-controller board: ag405rtk | Hardware board identifier of the flight controller ('ag405rtk'). | low |
| `version.svn_banner` | any | `^(?:Firmware svn info:\|Author\s*:\|Revision\s*:\|Modified\s*:)` | `Revision   :3337` | Firmware build information | Firmware source-control banner line (author/revision/modified flag of the firmware build). | low |
| `sys.conn_changed` | any | `^(?P<who>assistant\|app) connect changed:last\((?P<last>\d+)\) != current\((?P<cur>\d+)\)` | `assistant connect changed:last(255) != current(0)` | DJI Assistant (PC software): not connected | The connection state of the DJI Assistant (PC software) changed from 255 to 0 (255 = not yet known, 0 = not connected, 1 = connected). | low |
| `sys.math_lib` | [L-SYS] | `^MATH_LIB VERSION` | `[L-SYS]MATH_LIB VERSION: v1.0.0.1` | Math library version | Start-up message: version of the flight controller's math library. | low |
| `sys.user_priv` | [L-SYS] | `^set user priv (?P<value>\w+)` | `[L-SYS]set user priv 0xff` | User privilege level set | The flight controller set its user privilege value to 0xff (internal access level). | low |
| `fmu.device_registered` | [L-FMU/DM] | `^success reg dev type\s*(?P<type>\d+)\((?P<name>[^)]*)\), id:(?P<id>\d+)` | `[L-FMU/DM]success reg dev type  8(gyro_acc0), id:2, total num:2` | Device registered: gyro_acc0 | Start-up message: the flight controller's device manager registered device type 8 ('gyro_acc0') as id 2. | low |
| `hal.add_failed` | any | `^\[HAL\]\((?P<dev>[\w@]+)\):add to fmu failed ret = (?P<ret>\d+)` | `d2c<info-root>[HAL](tof0@0):add to fmu failed ret = 8` | Device registration failed: tof0@0 | The hardware layer failed to register device 'tof0@0' with the flight controller (return code 8). | normal |
| `gps.date` | any | `^read gps date:(?P<date>\d{8})` | `[L-DBG1]read gps date:20210419` | Date read from GPS: 2021-04-19 | The flight controller read the current date (2021-04-19) from the GPS receiver. | normal |
| `led.action` | [L-FMU/LED] | `^action changed\.\s*(?P<desc>.*?)\s*$` | `[L-FMU/LED]action changed. set home:(0)` | LED: home point recorded signal | The status LEDs switched to the 'home point set' pattern shown when the home point is recorded. (LED condition 'set home:(0)'.) | normal |
| `led.legacy` | any | `^\[LED\] changed:\s*(?P<desc>.+?)\s*$` | `[LED] changed: battery warning!` | LED: low-battery warning | The status LEDs switched to the low-battery warning pattern. (LED condition 'battery warning!'.) | high |
| `led.config` | [L-FMU/LED] | `^(?:type:\d+, normal flash action\|c\d+:\d+,\d+;\|set_open_led_action\|fill_open_led_action\|Reg from 0x\|change status in pm handler\|ce not sync changed\|not_sync_start_time)` | `[L-FMU/LED]c0:0,15;c1:0,3;c2:0,13;c3:2,3;c4:0,10;c5:0,3;c6:0,10;c7:0,3` | LED pattern configuration | Internal configuration of the status-LED patterns (colour/timing tables and registrations). | low |
| `api.multi_drone` | [L-API] | `^\[MUTI_DRONE\]` | `[L-API][MUTI_DRONE] receive muti_drone info: not_in_ctrl_flag:0 id:21` | Multi-aircraft control information | Information about multi-aircraft control (one controller/app managing several aircraft). | low |
| `api.cmd_index` | [L-API], [L-CMD] | `^(?:get_id_index_by_basic_cmd\|exec_fc_basic_function)` | `[L-API]get_id_index_by_basic_cmd: cmd 31 index 18` | Basic command dispatch | Internal dispatch of a basic flight-controller command (command index lookup/execution). | low |
| `sdk.startup` | [L-USER] | `^\[USER\]` | `[L-USER][USER]Read user config!` | SDK / user-data start-up message | Start-up message from the onboard SDK protocol or user-statistics module. | low |
| `fan.status` | [L-FAN] | `^fan` | `[L-FAN]fan cfg synced` | Cooling fan status | Start-up/status message from the cooling fan controller. | low |
| `fmu.inventory` | [L-FMU] | `^(?:\[DB\]FATAL ERROR haven't inited DB\|(?:v0 \|open \|sdk )?api num:\|db data num\|db_size\|debug module num\|sa module num\|code check num\|sys dev num\|dm table size\|\w* ?api num)` | `[L-FMU]api num: 193` | Flight-controller software inventory | Start-up message listing internal software component counts. A '[DB]FATAL ERROR haven't inited DB' line here is start-up ordering noise seen in most corpus logs. | low |
| `sim.param` | [L-SIM] | `^read parameter successfully` | `[L-SIM]read parameter successfully!` | Simulator parameters read | Start-up message: the built-in simulator module read its parameters (does not mean the simulator was used). | low |
| `sdk.protocol` | [L-SDK] | `^(?:\[USER\]\s*)?(?:Init_dji_api_protocol\|Baud_rate\|-+start sdk protocol)` | `[L-SDK][USER]----------start sdk protocol ---------` | Onboard SDK protocol start-up | Start-up message of the onboard SDK serial protocol. | low |
| `dev.internal` | [L-DEV] | `^(?:call comm_recorder_cfg\|add blockID\|\d+-cfg finished\|\d+-centerboard\|\d+-rtk set fix status\|\d+-record \d+-\d+)` | `[L-DEV]call comm_recorder_cfg sender:11 id 61761 res:0x06 len:230 item:57 4` | Device / recorder configuration message | Internal device-manager message (data-recorder configuration, centre-board version/reset, RTK fix status). | low |
| `boot.reset_cause` | any | `^reset by (?P<cause>power\|soft\|watchdog)\b` | `{root}INFO:reset by power.` | Flight controller started (power-on reset) | The flight controller recorded that it started because power was applied (a normal power-on). | normal |
| `boot.watchdog_reset` | any | `^\[panic\]:!+reset by watchdog!+` | `[panic]:!!!!!!!!!!!!!!!!!reset by watchdog!!!!!!!!!!!!!!!!!` | Flight controller reset by watchdog | The crash handler recorded that the last reset was caused by the watchdog timer: the software stopped responding and the processor restarted automatically. | high |
| `boot.crash_record_empty` | any | `^(?:\[panic\]\|\[hardfault\]):-+\s*(?P<what>.+?)\s+is null` | `{root}INFO:[panic]:-----fault info is null: addr(0x2045e800),flag(0xf4a9134f)-----` | No stored crash record (fault info) | At start-up the flight controller checks its crash-dump memory; 'fault info is null' means no saved crash/fault data of that kind was found. | low |
| `boot.crash_check` | any | `^(?:\[panic\]\|\[hardfault\]):\*+\s*check` | `{root}INFO:[panic]:******************check last trace ******************` | Crash-record check | Start-up step: the flight controller checks its crash-dump memory for a saved fault trace. | low |
| `boot.watchdog_cfg` | any | `^(?:iwdg_set_time_out\|wdg_set_swdg_time_out\|watchdog enable\|iwdg_set_max_timeout\|iwdg_set_swdg_time_out)` | `watchdog enable.` | Watchdog timer configured | Start-up message: the watchdog timer (which restarts the processor if the software hangs) was configured/enabled. | low |
| `boot.link_init` | any | `^(?:link_init <\d+> (?P<link>\w+)\|lib_route_init ok\|link_host_init ok\|__comm_cfg_init\((?P<link2>\w+)\)\|comm_rc_init\()` | `{root}INFO:link_init <4> link_ofdm, ret(0x0)` | Communication link initialised | Start-up message: an internal communication link/route of the flight controller was initialised. | low |
| `boot.init_tasks` | any | `^\[Init\]` | `[Init]HF task : 400Hz @ mode1` | Flight-controller task initialisation | Start-up message: flight-controller task scheduling initialised (task rates). | low |
| `boot.misc` | any | `^(?:i2c\d+ core recovery\|mp_state:\|dyn_map_mem_init\|battery collect param handler\|recorder \d+ configuare done\|samv7_backup_sram_alloc\|uuid_index\.flag\|cali_switch_config\|counter: \d+ timeout_counter)` | `{root}INFO:i2c1 core recovery` | Start-up / housekeeping message | Low-level start-up or housekeeping message from the flight-controller operating system (bus recovery, memory, recorder set-up). | low |
| `boot.clock_set` | any | `^(?:calender_setup_time\s+\|calender_time set type\((?P<type>\d+)\) to )\[(?P<date>[\d-]+)\]\s*(?P<time>[\d:]+)` | `d77<fly-ctrl>calender_setup_time  [2021-4-19]10:44:13. last_type(7),updates(0)` | Flight-controller clock set: 2021-4-19 10:44:13 | The flight controller set its calendar clock to 2021-4-19 10:44:13 (time zone not stated). Useful when correlating log times with real time. | normal |
| `boot.time_set_by` | any | `^set time by (?P<source>\d+), (?P<date>[\d-]+)\s+(?P<time>[\d:]+)` | `[L-DBG1]set time by 8, 2020-7-10  20:31:10` | Flight-controller clock set: 2020-7-10 20:31:10 | The flight controller set its clock to 2020-7-10 20:31:10 from time source 8 (source codes not documented; time zone not stated). | normal |
| `boot.time_check_err` | any | `^time_check type\((?P<type>\d+)\) err! (?P<when>.+)$` | `<FLY>time_check type(4) err! 2017-7-24 8:11:60` | Clock consistency check error | The flight controller's clock consistency check (type 4) flagged the time 2017-7-24 8:11:60 (note the seconds field 60). | low |
| `module.listed` | any | `^Op\.module\s+(?P<module>\S+)` | `Op.module\tL-GPS` | Log module listed: [L-GPS] | The log header lists firmware module [L-GPS] (GPS module), which may write lines to this log. | low |
| `module.string_def` | any | `^Op\.\[string\]` | `Op.[string] type\t32769 name\tDEBUG_MODULE` | Log format definition | Log header line defining the format of text records. | low |

## Configuration

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.app_rth_alt` | any (record kind: app_tip, app_warning) | `^Return to home altitude:\s*(?P<height>\d+)\s*M$` | `Return to home altitude:30M` | App: return-to-home altitude 30 m | Tip shown by the DJI app to the pilot: the return-to-home altitude is 30 m. | normal |
| `home.rth_height_stored` | [L-HOME] | `^target go-home height\((?P<height>\d+(?:\.\d+)?)\) is <= 500m and will be stored` | `[L-HOME]target go-home height(20) is <= 500m and will be stored` | Return-to-home altitude set to 20 m | A new return-to-home altitude of 20 m was accepted (it is within the 500 m maximum) and stored. On RTH the aircraft climbs to this height before flying home. | normal |
| `home.rth_height_result` | [L-HOME] | `^set go home height, result:(?P<result>-?\d+)` | `[L-HOME]set go home height, result:0` | Return-to-home altitude setting result 0 | Result code of the request to set the return-to-home altitude (0 conventionally indicates success). | low |
| `cfg.param_changed` | any | `^PM:\[(?P<source>[^\]]+)\]\s*"(?P<name>[^"]+)" to (?P<raw_value>\S+)` | `[L-CFG]PM:[Mobile APP] "g_status.farm.app_stop_motor_start" to 0` | Parameter changed: g_status.farm.app_stop_motor_start = 0 | The parameter manager changed parameter 'g_status.farm.app_stop_motor_start' to 0 (requested by 'Mobile APP'). | normal |
| `cfg.param_out_of_range` | any | `^PM:\[(?P<source>[^\]]+)\]\s*param is out of range,(?P<name>\S+)` | `[L-CFG]PM:[mission_info_record_to_user_log]param is out of range,farm_total_fly_miles` | Parameter value rejected (out of range): farm_total_fly_m... | The parameter manager rejected a value for 'farm_total_fly_miles' because it was outside the allowed range (requested by 'mission_info_record_to_user_log'). | low |
| `cfg.pm_internal` | any | `^(?:PM:cannot find the index by hash value\|PM:>>>>\|PM:add root work\|can't find var in opentable\|imu_cali_state_notify)` | `[L-CFG]PM:cannot find the index by hash value:4146467217` | Parameter-manager internal message | Internal parameter-manager message (lookup misses, table statistics). Not related to a pilot action. | low |
| `cfg.table_stats` | any | `^pbd cnt:\d+,item cnt:\d+,cmd cnt:\d+` | `pbd cnt:76,item cnt:1294,cmd cnt:108!` | Parameter table statistics | Size statistics of the flight controller's parameter table (start-up message). | low |

## Activation

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `activation.info` | [L-ACTIVATION] | `^(?:idx\|send index):(?P<idx>\d+)\s+(?:send )?id:(?P<id>\d+) get info,\s*(?:sta\|state):(?P<state>\d+),\s*date:(?P<date>[\d-]+),\s*time:(?P<time>[\d-]+),?\s*(?:proId:(?P<product_id>\S+))?` | `[L-ACTIVATION]idx:0 id:2 get info,sta:1,date:2020-3-5,time:20-54-10, proId:0UYKFBE0020` | Activation record: 2020-3-5 | The DJI activation module reported a stored activation record: state 1, date 2020-3-5, time 20:54:10, product ID 0UYKFBE0020. DJI aircraft must be activated through a DJI app/account before first flight; the log does not state the time zone. | normal |
| `activation.get_version` | [L-ACTIVATION] | `^get version` | `[L-ACTIVATION]get version` | Activation module version query | The activation module queried a version (start-up message). | low |
| `sys.real_name` | [L-SYS] | `^set_real_name_info,data:\s*(?P<epoch>\d+),\s*flag:\s*(?P<flag>\w+)` | `[L-SYS]set_real_name_info,data: 1594384270, flag: ab, len:14 14` | Real-name registration info set | The flight controller stored real-name (owner registration) information: data value 1594384270 (the number converts to 2020-07-10 12:31:10 UTC if read as a Unix time), flag ab. | normal |
| `sys.real_name_flag` | any | `^real_name_viechle_flag_init` | `[L-DBG1]real_name_viechle_flag_init:aa` | Real-name registration flag initialised | Start-up message: the aircraft's real-name registration flag was initialised. | low |

## Storage

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `recorder.timezone` | [L-RECORDER] | `^set timezone:(?P<tz>-?\d+), result:(?P<result>\d+)` | `[L-RECORDER]set timezone:80, result:0` | Recorder time zone set (value 80) | The flight-data recorder's time-zone value was set to 80 (encoding not documented in the log), result code 0. | normal |
| `storage.eeprom_load` | any | `^eeprom load\s+(?P<block>\d+)\s+(?P<offset>\d+)\s+(?P<size>\d+)\s+(?P<alloc>\d+)` | `0 eeprom load 12  448   52   56` | Settings block loaded from EEPROM | Start-up message: a block of stored settings was loaded from the flight controller's non-volatile memory (block 12). | low |
| `storage.eeprom_write` | any | `^Eeprom write offset:(?P<offset>[0-9a-fA-F]+)` | `Eeprom write offset:3b8` | Settings written to EEPROM | Stored settings were written to the flight controller's non-volatile memory (offset 0x3b8). | low |
| `storage.baseinfo_seq` | any | `^baseInfo seq error:this\[(?P<this>\d+)\]last\[(?P<last>\d+)\]` | `0 baseInfo seq error:this[10]last[0]` | Base-info record sequence gap | Internal logging message: a base-information record sequence number jumped (this 10, last 0). | low |
| `storage.cfg_write` | any | `^cfg write index (?P<index>\d+)` | `cfg write index 42` | Configuration record written | The system log recorded a configuration write (index 42). | low |
| `storage.write_timeout` | any | `write time_out` | `Info:2>write time_out : dma ,trace=0x0` | Storage write timeout | The storage driver reported a write timeout (data may be missing from the log around this time). | normal |

## Other

| Rule | Module tag | Pattern | Example (real line) | Title | Explanation | Importance |
|---|---|---|---|---|---|---|
| `txt.app_tip` | any (record kind: app_tip) | `^(?P<message>.+)$` | `Taking off` | App tip: Taking off | Message the DJI flight app displayed to the pilot as a tip, recorded verbatim (it may be in the app's display language). | normal |

## Configuration parameters

Lines of the form `[value] => name` or `[value][default][min][max][0xNN] => name` are configuration values dumped at the start of each DAT log. DroneDog shows them as `Configuration parameter <name> = <value>` (category configuration, importance low) and notes when the value differs from the firmware default. The `0xNN` field is a type/flag code and is not interpreted. Well-known parameters get a friendly name and importance normal:

| Parameter name(s) | Friendly name | Unit | Meaning | Value codes |
|---|---|---|---|---|
| `go_home_height`, `g_config.go_home.fixed_go_home_altitude` | Return-to-home altitude | m | Height the aircraft climbs to before flying back to the home point on return-to-home. |  |
| `g_config.flying_limit.max_height`, `limit_height_rel_cfg` | Maximum flight altitude limit | m | Maximum height above the take-off point the pilot allows the aircraft to fly. |  |
| `g_config.flying_limit.max_radius` | Maximum distance limit | m | Maximum horizontal distance from the home point when the distance limit is enabled. |  |
| `g_config.flying_limit.height_limit_enabled`, `g_config.advanced_function.height_limit_enabled` | Height limit enabled |  | Whether the altitude limit is enforced. | 0 = off, 1 = on |
| `g_config.flying_limit.radius_limit_enabled`, `g_config.advanced_function.radius_limit_enabled` | Distance limit enabled |  | Whether the maximum-distance limit is enforced. | 0 = off, 1 = on |
| `g_config.advanced_function.fail_safe_protection_enabled` | Failsafe protection enabled |  | Whether failsafe protection (action on remote-controller signal loss) is enabled. | 0 = off, 1 = on |
| `g_config.advanced_function.one_key_go_home_enabled` | One-key return-to-home enabled |  | Whether one-key (button) return-to-home is enabled. | 0 = off, 1 = on |
| `g_config.advanced_function.avoid_ground_enabled`, `g_config.flying_limit.avoid_ground_enabled` | Ground-avoidance enabled |  | Whether ground avoidance is enabled. | 0 = off, 1 = on |
| `g_config.flying_limit.user_avoid_ground_enable` | Landing protection / ground avoidance (user) |  | User setting for ground avoidance. | 0 = off, 1 = on |
| `rc_lost_action`, `g_config.fail_safe.protect_action` | Action on remote-controller signal loss |  | What the aircraft does when the remote-controller signal is lost (DJI Mobile SDK numbering). | 0 = hover, 1 = land, 2 = return-to-home |
| `rc_lost_action_time` | Delay before signal-loss action | s | Time the remote-controller signal must be lost before the failsafe action starts. |  |
| `rc_lost_enable` | Signal-loss failsafe enabled |  | Whether the signal-loss failsafe is enabled. | 0 = off, 1 = on |
| `novice_func_enabled`, `g_config.novice_cfg.novice_func_enabled` | Beginner mode enabled |  | Whether beginner (novice) mode, which restricts height, distance and speed, is enabled. | 0 = off, 1 = on |
| `novice_max_height`, `g_config.novice_cfg.max_height` | Beginner-mode height limit | m | Maximum height while beginner mode is on. |  |
| `novice_max_radius`, `g_config.novice_cfg.max_radius` | Beginner-mode distance limit | m | Maximum distance while beginner mode is on. |  |
| `dji_bat_level_1`, `g_config.voltage2.level_1_voltage` | Low-battery warning threshold | % | Battery percentage at which the low-battery (level 1) warning is given. |  |
| `dji_bat_level_2`, `g_config.voltage2.level_2_voltage` | Critical-battery warning threshold | % | Battery percentage at which the critical (level 2) warning is given. |  |
| `bat_cap_v1_prot_type`, `g_config.voltage2.level_1_function` | Action at low-battery warning |  | What the aircraft does at the low-battery (level 1) threshold (codes as commonly documented for DJI flight controllers). | 0 = warning only, 1 = return-to-home, 2 = land |
| `bat_cap_v2_prot_type`, `g_config.voltage2.level_2_function` | Action at critical-battery warning |  | What the aircraft does at the critical (level 2) threshold (codes as commonly documented). | 0 = warning only, 1 = return-to-home, 2 = land |
| `raw_bat_level_1`, `g_config.voltage.level_1_protect` | Low-voltage warning threshold | mV | Voltage at which the low-voltage (level 1) protection triggers. |  |
| `raw_bat_level_2`, `g_config.voltage.level_2_protect` | Critical-voltage threshold | mV | Voltage at which the critical-voltage (level 2) protection triggers. |  |
| `bat_level_1_action`, `g_config.voltage.level_1_protect_type` | Action at low-voltage warning |  | What the aircraft does at the low-voltage threshold (codes as commonly documented). | 0 = warning only, 1 = return-to-home, 2 = land |
| `bat_level_2_action`, `g_config.voltage.level_2_protect_type` | Action at critical-voltage warning |  | What the aircraft does at the critical-voltage threshold (codes as commonly documented). | 0 = warning only, 1 = return-to-home, 2 = land |
| `ensmartbatgh`, `g_config.voltage2.user_set_smart_bat` | Smart return-to-home (battery) setting |  | User setting for Smart (battery-based) return-to-home. |  |
| `level1_smart_battert_gohome`, `g_config.voltage2.level1_smart_battert_gohome` | Smart-RTH battery reserve | % | Battery reserve used by the smart low-battery return-to-home logic. |  |
| `level2_smart_battert_land`, `g_config.voltage2.level2_smart_battert_land` | Smart-landing battery reserve | % | Battery reserve used by the smart low-battery landing logic. |  |
| `enable_multiple_mode`, `g_config.control.multi_control_mode_enable` | Multiple flight modes enabled |  | Whether 'multiple flight modes' is enabled, which allows ATTI/Sport positions of the flight-mode switch. | 0 = off, 1 = on |
| `fswitch_selection`, `g_config.control.control_mode[0]` | Flight-mode switch position 1 mode |  | Flight mode assigned to switch position 1 (internal mode code). |  |
| `fswitch_selection_1`, `g_config.control.control_mode[1]` | Flight-mode switch position 2 mode |  | Flight mode assigned to switch position 2 (internal mode code). |  |
| `fswitch_selection_2`, `g_config.control.control_mode[2]` | Flight-mode switch position 3 mode |  | Flight mode assigned to switch position 3 (internal mode code). |  |
| `go_home_heading`, `g_config.go_home.go_home_heading_option` | Return-to-home heading option |  | How the aircraft orients itself while returning home (internal option code). |  |
| `adv_gohome_enable`, `g_config.go_home.adv_gohome_enable` | Advanced return-to-home enabled |  |  | 0 = off, 1 = on |
| `avoid_enable`, `g_config.go_home.avoid_enable` | Obstacle avoidance during RTH enabled |  |  | 0 = off, 1 = on |
| `user_avoid_enable`, `g_config.avoid_obstacle_limit_cfg.user_avoid_enable` | Obstacle avoidance enabled (user) |  | User setting for obstacle avoidance. | 0 = off, 1 = on |
| `g_config.avoid_obstacle_limit_cfg.avoid_obstacle_enable` | Obstacle avoidance enabled |  |  | 0 = off, 1 = on |
| `close_go_home_height` | Return-to-home altitude when close to home | m | Height used for return-to-home when the aircraft is close to the home point. |  |
| `limit_height_abs` | Absolute height limit | m | Absolute height limit value. |  |
| `limit_height_abs_without_gps` | Height limit without GPS | m | Height limit applied when no GPS is available. |  |
| `g_config.airport_limit_cfg.cfg_search_radius` | Airport no-fly-zone search radius | m | Radius within which the fly-safe logic searches for airports. |  |
| `g_status.user_info.statistical_info.total_distance` | Lifetime total flight distance | m | Cumulative flight distance stored by the aircraft (as recorded; unit consistent with metres). |  |
| `g_status.user_info.statistical_info.total_motor_start_time`, `g_status.user_info.statistical_info.total_motor_start_t` | Lifetime total motor-on time | s | Cumulative motor running time stored by the aircraft (as recorded; unit consistent with seconds). |  |
| `g_raw.battery_info.serial_number` | Battery serial number |  | Serial number reported by the smart battery. |  |
| `smart_bat_basic_cap` | Smart-battery basic capacity reserve | % |  |  |
| `bat_enable_smart_bat_landing_protect` | Smart-battery landing protection enabled |  |  | 0 = off, 1 = on |
| `g_config.fdi.gps_max_horizontal_pos_over_count` | FDI: GPS horizontal-position outlier count limit |  | Flight-controller fault-detection (FDI) threshold: how many consecutive times the GPS horizontal position may exceed its limits before GPS is treated as faulty. |  |
| `g_config.fdi.gps_max_horizontal_vel_over_count` | FDI: GPS horizontal-velocity outlier count limit |  | Fault-detection threshold: consecutive GPS horizontal-velocity exceedances tolerated before GPS is treated as faulty. |  |
| `g_config.fdi.gps_max_horizontal_pos_diff` | FDI: max GPS horizontal-position difference |  | Fault-detection threshold for the allowed difference in GPS horizontal position (unit not stated; consistent with metres). |  |
| `g_config.fdi.max_horizontal_vel_fusion_error` | FDI: max horizontal-velocity fusion error |  | Fault-detection threshold for the allowed horizontal-velocity error of the sensor-fusion estimate. |  |
| `tripod_func_enabled`, `g_config.mode_tripod_en_cfg.tripod_func_enabled` | Tripod mode enabled |  |  | 0 = off, 1 = on |

Other parameters are labelled by their name prefix:

| Name prefix | Described as |
|---|---|
| `g_config.fdi.` | Flight-controller fault-detection (FDI) parameter |
| `g_config.go_home.` | Return-to-home setting |
| `g_config.voltage` | Battery/voltage protection setting |
| `g_config.flying_limit.` | Flight-limit setting |
| `g_config.novice_cfg.` | Beginner-mode setting |
| `g_config.mode_sport_cfg.` | Sport-mode tuning |
| `mode_sport_cfg` | Sport-mode tuning |
| `g_config.mode_tripod` | Tripod-mode tuning |
| `mode_tripod_cfg` | Tripod-mode tuning |
| `g_config.avoid_obstacle_limit_cfg.` | Obstacle-avoidance setting |
| `avoid_` | Obstacle-avoidance setting |
| `g_config.hotpoint_cfg.` | Point-of-Interest (hotpoint) setting |
| `g_config.followme_cfg.` | Follow-me setting |
| `g_config.home_lock_cfg.` | Home-lock setting |
| `g_config.airport_limit_cfg.` | Airport no-fly-zone setting |
| `g_config.imu_para_cfg.` | IMU / navigation-filter setting |
| `g_config.imu_dir` | IMU mounting orientation |
| `g_config.receiver.` | Remote-control receiver calibration |
| `g_config.aircraft.` | Airframe / motor-mixing setting |
| `g_config.api_entry_cfg.` | SDK/API interface setting |
| `g_config.serial_api_cfg.` | SDK/API interface setting |
| `g_config.control.` | Flight-control setting |
| `g_config.fail_safe.` | Failsafe setting |
| `g_config.advanced_function.` | Advanced-function switch |
| `g_config.output_servo.` | Servo/motor output setting |
| `g_config.intelligence_orientation.` | Intelligent Orientation Control (IOC) setting |
| `g_config.` | Flight-controller configuration setting |
| `g_raw.gps_snr.` | GPS satellite signal strength (SNR) snapshot |
| `g_raw.battery_info.` | Battery information |
| `g_raw.` | Raw sensor value snapshot |
| `g_status.user_info.statistical_info` | Aircraft usage statistics |
| `g_status.` | Flight-controller status value |
| `g_cfg_debug.` | Debug configuration |
| `fix_send` | Data-logging stream configuration |
| `cfg_actuator_` | Motor/actuator output setting |
| `esc_` | Motor/ESC setting |
| `motor_` | Motor setting |
| `f_chl_` | Remote-control channel mapping |
| `r_rc_` | Remote-control channel mapping |
| `farm_` | Agricultural (spraying) statistic/setting |
| `sweep_` | Test-signal (frequency sweep) setting |
| `range.` | Parameter range metadata |
| `go_home` | Return-to-home setting |
| `gohome` | Return-to-home setting |
| `novice` | Beginner-mode setting |
| `bat` | Battery setting |
| `rc_` | Remote-control setting |
| `limit_` | Flight-limit setting |
| `gps` | GPS setting |
| `compass` | Compass setting |
| `imu` | IMU setting |

## Reference tables

### App-log flight modes

| Recorded name | DJI app name | Meaning |
|---|---|---|
| `Manual` | Manual | manual mode: no stabilisation assistance |
| `Atti` | ATTI | attitude mode: the aircraft self-levels and holds height but does not hold position, so it drifts with the wind |
| `AttiCourseLock` | ATTI course lock | attitude mode with course lock |
| `AttiHover` | ATTI hover | attitude-mode hover (no position hold) |
| `Hover` | P-GPS hover | hovering with satellite position hold |
| `GPSBlake` | P-GPS braking | GPS mode while braking |
| `GPSAtti` | P-GPS | normal GPS-assisted flight (positioning mode): the aircraft holds position when the sticks are released |
| `GPSCourseLock` | Course Lock | GPS course-lock intelligent orientation mode |
| `GPSHomeLock` | Home Lock | GPS home-lock intelligent orientation mode |
| `GPSHotPoint` | Point of Interest | orbiting a point of interest (hotpoint) |
| `AssistedTakeoff` | Assisted take-off | assisted take-off |
| `AutoTakeoff` | Auto take-off | automatic take-off |
| `AutoLanding` | Auto landing | automatic landing |
| `AttiLanding` | ATTI landing | landing in attitude mode |
| `GPSWaypoint` | Waypoint mission | flying a waypoint mission |
| `GoHome` | Return-to-home | returning to the home point |
| `ClickGo` | Click-Go | Click-Go |
| `Joystick` | Joystick | joystick (virtual stick/SDK) control |
| `GPSAttiWristband` | Wristband | wristband control |
| `Cinematic` | Cinematic | cinematic mode |
| `AttiLimited` | ATTI limited | limited attitude mode |
| `Draw` | Draw | Draw intelligent flight mode |
| `GPSFollowMe` | Follow Me | Follow Me intelligent flight mode |
| `ActiveTrack` | ActiveTrack | ActiveTrack subject tracking |
| `TapFly` | TapFly | TapFly |
| `Pano` | Panorama | panorama capture |
| `Farming` | Farming | agricultural spraying mode |
| `FPV` | FPV | FPV mode |
| `GPSSport` | Sport | Sport mode: higher speeds; obstacle sensing may be disabled on many models |
| `GPSNovice` | Beginner | beginner (novice) mode |
| `ConfirmLanding` | Landing confirmation | the aircraft is near the ground and waiting for the pilot to confirm landing (landing protection) |
| `TerrainTracking` | Terrain follow | terrain following |
| `NaviAdvGoHome` | Advanced RTH | advanced return-to-home |
| `NaviAdvLanding` | Advanced landing | advanced landing |
| `Tripod` | Tripod | Tripod (slow, precise) mode |
| `TrackHeadlock` | QuickShot/ActiveTrack | ActiveTrack/QuickShot (track head-lock) |
| `EngineStart` | Motors starting | the motors have just been started |
| `GPSGentle` | Gentle | gentle GPS mode |

### App-log flight-controller actions

| Recorded name | Title | Meaning |
|---|---|---|
| `WarningPowerGoHome` | Low-battery return-to-home | Return-to-home started because the battery reached the low-battery warning level. |
| `WarningPowerLanding` | Low-battery landing | Landing started because the battery reached the low-battery warning level. |
| `SmartPowerGoHome` | Smart low-battery return-to-home | Return-to-home started because the flight controller calculated that the remaining battery was only just sufficient to return home (Smart RTH). |
| `SmartPowerLanding` | Smart low-battery landing | Landing started because the flight controller calculated that the remaining battery was only sufficient to land. |
| `LowVoltageLanding` | Low-voltage landing | Landing started because the battery voltage was low. |
| `LowVoltageGoHome` | Low-voltage return-to-home | Return-to-home started because the battery voltage was low. |
| `SeriousLowVoltageLanding` | Critical-battery forced landing | Forced landing started because the battery reached the critical (serious low) level. |
| `RCOnekeyGoHome` | RTH from remote-controller button | Return-to-home was started from the remote controller's RTH button. |
| `RCAssistantTakeoff` | Assisted take-off from remote controller | An assisted take-off was started from the remote controller. |
| `RCAutoTakeoff` | Auto take-off from remote controller | An automatic take-off was started from the remote controller. |
| `RCAutoLanding` | Auto landing from remote controller | An automatic landing was started from the remote controller. |
| `AppAutoGoHome` | RTH from the app | Return-to-home was started from the mobile app. |
| `AppAutoLanding` | Auto landing from the app | An automatic landing was started from the mobile app. |
| `AppAutoTakeoff` | Auto take-off from the app | An automatic take-off was started from the mobile app. |
| `OutOfControlGoHome` | Signal-lost return-to-home (failsafe) | Return-to-home started because the remote-controller signal was lost (failsafe RTH). |
| `ApiAutoTakeoff` | Auto take-off from SDK app | An automatic take-off was commanded through the DJI SDK (third-party app or onboard computer). |
| `ApiAutoLanding` | Auto landing from SDK app | An automatic landing was commanded through the DJI SDK. |
| `ApiAutoGoHome` | RTH from SDK app | Return-to-home was commanded through the DJI SDK. |
| `AvoidGroundLanding` | Landing (ground avoidance) | Landing triggered by the ground-avoidance logic. |
| `AirportAvoidLanding` | Forced landing in no-fly zone | Landing triggered because the aircraft was inside or approaching an airport/no-fly zone. |
| `TooCloseGoHomeLanding` | RTH too close: landing instead | Return-to-home was requested very close to the home point, so the aircraft landed in place. |
| `TooFarGoHomeLanding` | RTH too far: landing instead | Return-to-home was requested too far from the home point, so the aircraft landed instead. |
| `AppWPMission` | Waypoint mission from app | A waypoint mission started from the app. |
| `WPAutoTakeoff` | Waypoint mission auto take-off | Automatic take-off as part of a waypoint mission. |
| `GoHomeAvoid` | RTH obstacle avoidance | Obstacle avoidance during return-to-home. |
| `PGoHomeFinish` | Return-to-home finished | The return-to-home sequence finished. |
| `VertLowLimitLanding` | Landing at low height limit | Automatic landing started when the aircraft descended to its lower height limit near the ground. |
| `BatteryForceLanding` | Battery forced landing | Forced landing triggered by the battery protection. |
| `MCProtectGoHome` | Flight-controller protective RTH | Return-to-home triggered by flight-controller protection logic. |
| `MotorblockLanding` | Landing: motor blocked | Landing triggered because a motor was detected as blocked. |
| `AppRequestForceLanding` | Forced landing requested by app | A forced landing was requested by the app. |
| `FakeBatteryLanding` | Landing: battery not genuine | Landing triggered because the battery failed authentication. |
| `RTHComingObstacleLanding` | RTH obstacle: landing | Landing triggered by an obstacle during return-to-home. |
| `IMUErrorRTH` | IMU error return-to-home | Return-to-home triggered by an IMU error. |

### App-log aircraft fault flags

| Flag | Title | Meaning |
|---|---|---|
| `compass_error` | Compass error | The aircraft reported a compass error (e.g. magnetic interference or calibration problem). |
| `barometer_dead_in_air` | Barometer failure in flight | The aircraft reported that its barometer stopped working while airborne. |
| `motor_blocked` | Motor blocked | The aircraft reported a blocked motor. |
| `not_enough_force` | Not enough thrust | The aircraft reported it could not produce enough thrust (DJI app: 'not enough force'), e.g. damaged propeller or motor, overload, or weak battery. |
| `propeller_catapult` | Propeller came off | The aircraft reported a propeller ejected/loose (propeller catapult flag). |
| `accelerometer_over_range` | Accelerometer over range | The accelerometer reading exceeded its measuring range (violent motion or impact). |
| `vibrating` | Excessive vibration | The aircraft reported abnormal vibration. |
| `out_of_limit` | Flight limit reached | The aircraft reported it reached a configured height or distance limit. |
| `obstacle_sensor_error` | Obstacle/ultrasonic sensor error | The aircraft reported an error in its downward ultrasonic/obstacle sensor. |

### Status-LED conditions (`[L-FMU/LED]action changed.` / `[LED] changed:`)

| Condition text contains | Title | Meaning | Importance |
|---|---|---|---|
| 'battery warning', 'batt warning', 'battery_status' | LED: low-battery warning | The status LEDs switched to the low-battery warning pattern. | high |
| 'rc completely lost', 'rc lost' | LED: remote-controller signal lost | The status LEDs switched to the pattern for a completely lost remote-controller signal. | high |
| 'fly limit', 'airport' | LED: flight-limit / no-fly-zone warning | The status LEDs switched to the flight-limit pattern (the firmware's own label gives airports as an example). | high |
| 'near hori', 'near vert' | LED: near height/distance limit | The status LEDs switched to the pattern shown when the aircraft is near its horizontal (distance) or vertical (height) limit. | high |
| 'set home' | LED: home point recorded signal | The status LEDs switched to the 'home point set' pattern shown when the home point is recorded. | normal |
| 'compass out of range', 'compass' | LED: compass warning | The status LEDs switched to the compass-problem pattern (compass reading out of range). | normal |
| 'no atti' | LED: attitude not available | The status LEDs switched to the 'no attitude' pattern: the attitude estimate is not yet initialised/converged (normal for a short time after power-on). | normal |
| 'prevent motor on', 'cannot start motor' | LED: motor start prevented | The status LEDs show that starting the motors is currently prevented. | normal |
| 'temperature not ready' | LED: IMU warming up | The status LEDs show that the IMU temperature is not yet ready after start-up (warm-up). | low |
| 'forearm led' | LED: front arm lights switched off by camera | The front (forearm) LEDs were switched off at the camera's request; DJI aircraft can turn the front LEDs off while recording or shooting. | low |
| 'test led' | LED: start-up self-test | The status LEDs ran their start-up test pattern. | low |
| 'normal flash', 'normal led' | LED: normal status pattern | The status LEDs returned to their normal flashing pattern. | low |
| 'farm app stop motor' | LED: app motor stop/start indication | LED condition related to the agricultural app's motor stop/start request. | low |
| 'on_ctrl', 'not_ctrl', 'select', 'pre_sel' | LED: control/selection indication | LED pattern for the aircraft's control/selection state (used when a controller selects or controls the aircraft). | low |
| 'ced', 'back ce' | LED: status pattern change | LED pattern change for an internal LED condition (label 'ced'); its meaning is not publicly documented. | low |

### Fault-detection (FDI) navigation sources and fault reasons

| Token | Meaning |
|---|---|
| `GPS` | GPS |
| `RTK` | RTK GNSS |
| `VO` | vision positioning |
| `ULTRASONIC` | ultrasonic height sensor |
| `FUSION` | sensor-fusion estimate |
| `BARO` | barometer |
| `MAG` | compass |
| `MAGN` | compass |
| `IMU` | IMU |
| `COMPASS` | compass |
| `conformity` | fault reason: its data disagreed with the other navigation sources |
| `disconnect` | fault reason: no data was received (disconnected) |
| `stuck_vel` | fault reason: its velocity output stopped changing (stuck) |
| `stuck` | fault reason: its output stopped changing (stuck) |
| `abrupt_pos` | fault reason: its position jumped abruptly |
| `no_atti` | fault reason: no attitude solution was available |
| `not_converge` | fault reason: the estimate had not converged |

### Battery protection states (`[L-BATTERY]old:X=>>>>new:Y`)

| State | Meaning |
|---|---|
| `ON_GROUND` | on the ground |
| `SAFE_FLY` | normal flight (no battery action) |
| `GOHOME_CNTDOWN` | countdown before low-battery return-to-home |
| `GOHOME` | low-battery return-to-home |
| `LANDING` | low-battery landing |
| `FORCE_LANDING` | forced landing |

## Fallbacks

Lines that match no rule but carry a known module tag are labelled with the module only (rule id `fallback:tag:<tag>`, e.g. `[L-GPS]` -> "GPS module message"), with an explicit note that no further meaning should be inferred. Known module tags:

| Tag | Category | Module |
|---|---|---|
| `[L-FMU]` | system | flight-management unit (core flight controller) |
| `[L-FMU/LED]` | system | status-LED controller |
| `[L-FMU/MOTOR]` | motors | motor manager |
| `[L-FMU/FSM]` | flight | flight state machine |
| `[L-FMU/DM]` | system | device manager |
| `[L-FMU/VERSION]` | system | firmware/version banner |
| `[L-FMU/TOPOLOGY]` | system | device topology |
| `[L-FMU/WRITE]` | storage | log writer |
| `[L-FMU/CODE]` | system | code integrity check |
| `[L-FLYMODE]` | flight | flight-mode logic |
| `[L-HOME]` | home_point | home-point module |
| `[L-BATTERY]` | battery | battery module |
| `[L-GPS]` | gps | GPS module |
| `[L-RTK]` | gps | RTK (high-precision GNSS) module |
| `[L-COMPASS]` | compass | compass module |
| `[L-FDI]` | sensors | fault detection and isolation (FDI) |
| `[L-NS]` | navigation | navigation system / attitude estimator |
| `[L-RC]` | radio_link | remote-controller module |
| `[L-API]` | system | SDK/API interface |
| `[L-SDK]` | system | onboard SDK |
| `[L-ACTIVATION]` | activation | activation module |
| `[L-DEV]` | system | device module |
| `[L-SYS]` | system | system module |
| `[L-SYS_ERR]` | system | system error module |
| `[L-CFG]` | configuration | parameter/configuration manager |
| `[L-GEO]` | safety | GEO fly-safe (no-fly-zone) module |
| `[L-FLYLIMIT]` | safety | flight-limit module |
| `[L-WHITELIST]` | safety | fly-safe unlock (whitelist) module |
| `[L-ADS-B]` | safety | ADS-B (manned aircraft detection) module |
| `[L-ESC]` | motors | electronic speed controller (ESC) module |
| `[L-PWM]` | motors | motor output (PWM) module |
| `[L-VO]` | sensors | vision positioning module |
| `[L-VISION]` | sensors | vision system |
| `[L-TOF]` | sensors | time-of-flight distance sensor |
| `[L-RADAR]` | sensors | radar module |
| `[L-AVOID]` | safety | obstacle-avoidance module |
| `[L-SIM_AVOIDANCE]` | system | simulator obstacle-avoidance module |
| `[L-TOPO]` | system | device topology module |
| `[L-MIS]` | navigation | mission (waypoint) module |
| `[L-N_MIS]` | flight | flight-task manager |
| `[L-WP2]` | navigation | waypoint mission module |
| `[L-POI]` | navigation | point-of-interest module |
| `[L-CAMERA]` | camera_gimbal | camera control |
| `[L-ZENMUSE]` | camera_gimbal | Zenmuse camera/gimbal |
| `[L-GIMBAL]` | camera_gimbal | gimbal module |
| `[L-GYRO_ACC]` | imu | gyroscope/accelerometer driver |
| `[L-IMU]` | imu | IMU module |
| `[L-SENSOR]` | sensors | sensor hub |
| `[L-PITOT]` | sensors | airspeed (pitot) sensor |
| `[L-TAKEOFF]` | flight | take-off module |
| `[L-ON_HAND_TAKEOFF]` | flight | hand-launch take-off module |
| `[L-CTRL]` | flight | flight-control module |
| `[L-OSD]` | system | on-screen-display data module |
| `[L-SIM]` | system | simulator module |
| `[L-USER]` | system | user statistics module |
| `[L-DBG1]` | system | debug output |
| `[L-DBG2]` | system | debug output |
| `[L-FAN]` | system | cooling fan controller |
| `[L-IOC]` | navigation | Intelligent Orientation Control (course/home lock) |
| `[L-CMD]` | system | command dispatcher |
| `[L-SEND DATA]` | radio_link | telemetry push module |
| `[L-GEAR]` | flight | landing-gear module |
| `[L-DROPSAFE]` | other | drop-safe (agricultural) module |
| `[L-SPRAY_SYS]` | other | spraying system (agricultural) |
| `[L-FARM]` | other | agricultural (farm) module |
| `[L-AES]` | system | encryption module |
| `[L-HAL_LINK]` | system | hardware link layer |
| `[L-SA]` | system | sensor abstraction layer |
| `[L-CENTER_BOARD]` | system | centre board |
| `[L-PROTOCOL]` | system | protocol module |
| `[L-AGENT]` | system | agent module |
| `[L-EMBEDDED]` | system | embedded system module |
| `[L-EEPROM]` | storage | non-volatile memory (EEPROM) |
| `[L-RECORDER]` | storage | flight-data recorder |
| `[L-AP]` | system | application processor link |
| `[L-WRISTBAND]` | radio_link | wristband controller |
| `[L-HEADSET]` | radio_link | headset (goggles) link |
| `[L-AUTOTEST]` | system | factory self-test |
| `[L-MAP]` | navigation | map module |
| `[L-WL]` | radio_link | wireless link |
| `[L-INFO_SHARE]` | system | information-sharing module |
| `[L-USB]` | system | USB module |
