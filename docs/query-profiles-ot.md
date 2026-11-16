# ICS / SCADA / OT query definitions

Generated from the current executable catalog. These are indexed candidates on any port; protocol and device classification are separate evidence classes. Provider aliases and result recall were not live-validated. Reference port modes are optional; PLC has no universal reference port.

| Profile | Transport hint | Shodan candidate | ZoomEye candidate | Netlas candidate |
| --- | --- | --- | --- | --- |
| Modbus TCP | tcp | `shodan.module:modbus` | `(service="modbus" \|\| service="modbus-tcp")` | `modbus.function_code:43 AND modbus.length:>0` |
| BACnet/IP | udp | `shodan.module:bacnet` | `(service="bacnet" \|\| service="bacnet-ip")` | `bacnet.is_bacnet:true` |
| Siemens S7 over ISO-on-TCP | tcp | `shodan.module:s7` | `(service="s7" \|\| service="s7comm" \|\| service="siemens-s7")` | `s7.module:* AND s7.module_type:*` |
| OPC UA TCP | tcp | `shodan.module:opc-ua` | `(service="opc-ua" \|\| service="opcua")` | `prot7:("opc-ua" OR "opcua")` |
| EtherNet/IP explicit messaging | tcp | `shodan.module:ethernetip` | `(service="ethernetip" \|\| service="ethernetip-1" \|\| service="ethernet-ip" \|\| service="enip")` | `prot7:("ethernetip" OR "ethernetip-1" OR "ethernet-ip" OR "enip")` |
| EtherNet/IP I/O | udp | `shodan.module:ethernetip-udp` | `protocol="UDP" && (service="ethernetip" \|\| service="ethernet-ip" \|\| service="enip")` | `prot4:udp AND prot7:("ethernetip" OR "ethernet-ip" OR "enip")` |
| DNP3 | tcp | `shodan.module:dnp3` | `service="dnp3"` | `prot7:("dnp3")` |
| IEC 60870-5-104 | tcp | `shodan.module:iec-104` | `(service="iec-104" \|\| service="iec104" \|\| service="iec-60870-5-104" \|\| service="iec60870-5-104")` | `prot7:("iec-104" OR "iec104" OR "iec-60870-5-104" OR "iec60870-5-104")` |
| IEC 61850 MMS | tcp | `shodan.module:iec-61850` | `(service="iec-61850" \|\| service="iec61850" \|\| service="iec61850-mms")` | `prot7:("iec-61850" OR "iec61850" OR "iec61850-mms")` |
| Omron FINS | udp | `shodan.module:omron-tcp` | `(service="fins" \|\| service="omron-fins" \|\| service="omron-tcp")` | `prot7:("fins" OR "omron-fins" OR "omron-tcp")` |
| Mitsubishi MELSEC / SLMP | udp | `shodan.module:melsec-q-tcp,melsec-q-udp` | `(service="melsec" \|\| service="melsec-q" \|\| service="melsec-q-tcp" \|\| service="melsec-q-udp")` | `prot7:("melsec" OR "melsec-q" OR "melsec-q-tcp" OR "melsec-q-udp")` |
| Beckhoff ADS | tcp | `"WEB SERVER V" "title: BC"` | `((service="ads" \|\| service="beckhoff-ads")) \|\| (http.header="WEB SERVER V" && http.header="title: BC")` | `(prot7:("ads" OR "beckhoff-ads")) OR (http.headers.server:"WEB SERVER V" AND http.unknown_headers.key:title AND http.unknown_headers.value:BC*)` |
| Niagara Fox | tcp | `shodan.module:fox,secure-fox` | `(service="niagara-fox" \|\| service="fox" \|\| service="secure-fox")` | `prot7:("niagara-fox" OR "fox" OR "secure-fox")` |
| HART-IP | udp | `shodan.module:hart-ip-udp` | `(service="hart-ip" \|\| service="hartip")` | `prot7:("hart-ip" OR "hartip")` |
| CODESYS gateway | tcp | `shodan.module:codesys` | `service="codesys"` | `prot7:("codesys")` |
| ProConOS | tcp | `shodan.module:proconos` | `service="proconos"` | `prot7:("proconos")` |
| Phoenix Contact PC Worx | tcp | `shodan.module:pcworx` | `(service="pcworx" \|\| service="pc-worx")` | `prot7:("pcworx" OR "pc-worx")` |
| GE SRTP | tcp | `shodan.module:general-electric-srtp` | `(service="general-electric-srtp" \|\| service="ge-srtp")` | `prot7:("general-electric-srtp" OR "ge-srtp")` |
| PLC device classification | tcp | `device:plc` | `device="plc"` | No verified generic device-class field exists in the checked Netlas response schema. Software tags are not equivalent to a PLC device classification; use a protocol preset instead. |
| Lantronix TCP setup | tcp | `"Lantronix Universal Device Server"` | `protocol="TCP" && service="lantronix"` | `prot4:tcp AND prot7:"lantronix"` |
| Lantronix UDP setup / discovery | udp | `shodan.module:lantronix-udp` | `protocol="UDP" && (service="lantronix-config" \|\| service="lantronix_config")` | `prot4:udp AND prot7:("lantronix-config" OR "lantronix_config")` |
| SNMP SMUX | tcp | No Shodan preset: SMUX uses binary BER responses; the checked scanner list has no SMUX module. A usable all-port search requires a documented SMUX classifier, indexed decoded field, or representative indexed banner signature. The short binary ClosePDU is not a verified searchable text fingerprint; SNMP or port-only filters would not establish SMUX. | `service="smux"` | `prot7:("smux")` |
| HID VertX / EDGE discovery | udp | `shodan.module:vertx-edge` | `protocol="UDP" && (service="vertx-edge" \|\| service="vertx")` | `prot4:udp AND prot7:("vertx-edge" OR "vertx")` |

Transport is a capture hint; observations retain actual transport. EthernetIP UDP family does not verify cyclic I/O; FINS Shodan identifies its TCP companion. SMUX is not ICS-specific. PLC device classification does not establish S7, Modbus, vendor or physical hardware. New OT profiles use existing generic TCP or explicit manual unicast UDP capture, not newly implemented OT native adapters.

Community review leads: [ZoomEye products](https://github.com/selmux/Alhasawi-ICS-OT-Security-projetcs/blob/main/ICS-OT-IoT%20dorks/ZoomEye-OT-Products), [FOFA examples](https://github.com/selmux/Alhasawi-ICS-OT-Security-projetcs/blob/main/ICS-OT-IoT%20dorks/FOFA-OT-Products-Protocols), [ICSRank](https://www.icsrank.com/dorks). FOFA grammar, product taxonomies and port filters were not copied blindly. ICSRank inventory API returned403; dataset not evaluated.

Authoritative field grammar: [Shodan filters](https://www.shodan.io/search/filters), [scanner protocols](https://developer.shodan.io/api#shodan-protocols), [ZoomEye API](https://github.com/zoomeye-ai/ZoomEye-python/blob/master/api.md), [Netlas fields](https://docs.netlas.io/knowledge-base/field-reference/responses/). Checked snapshot: `tests/fixtures/shodan-protocols-20261107.json`.
