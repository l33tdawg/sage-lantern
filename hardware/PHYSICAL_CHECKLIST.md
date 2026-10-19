# Physical verification checklist

Use this at the bench. Record what you see, not what the part was supposed to
be. Photograph each connector with a ruler or a scale reference.

## Unit identity

| Field | Record |
|---|---|
| Unit name | |
| Board model and revision | |
| Board serial (private record only) | |
| Installed OS and kernel | |
| NVMe model and size | |
| Power supply and measured voltage | |
| Date and person | |

## ROCK 5B+ DSI path

| Check | Record |
|---|---|
| DSI connector contact count | |
| Connector pitch and width | |
| Waveshare panel FPC contact count | |
| Panel FPC pitch and width | |
| Adapter part number | |
| Adapter board-side and panel-side counts | |
| 5 V lead connector and polarity | |
| Touch I2C bus | |
| Panel lights and touch respond | |

Do not order the adapter until the two contact counts and the lane mapping are
written down.

## Orange Pi 5 Plus CSI path

| Check | Record |
|---|---|
| CSI connector contact count | |
| Connector pitch and orientation | |
| IMX519 module FPC contact count | |
| Adapter part number | |
| Adapter has I2C level shifting | yes / no |
| Kernel driver loaded | |
| Device-tree overlay | |
| Still image captured | |

## Radios and GNSS

| Check | Record |
|---|---|
| Primary HAT model and band | |
| 40-pin mapping confirmed | |
| Antenna fitted before transmit | yes / no |
| XIAO/Wio kit received | yes / no |
| XIAO firmware and USB identity | |
| GNSS antenna connected | yes / no |
| gpsd mode, satellites, HDOP | |
| Real fix captured | yes / no |

## Power and thermals

| Check | Record |
|---|---|
| Supply type and rating | |
| Idle power | |
| Loaded power | |
| SoC temperature at idle | |
| SoC temperature under load | |
| Brownout or reset observed | yes / no |

## Storage and software

| Check | Record |
|---|---|
| Inventory script output saved | |
| Root filesystem on NVMe | yes / no |
| Services active | |
| Map packs listed | |
| Chat answer produced | |
| Voice answer produced | |
| Camera answer produced | |

Attach the files and photographs to the unit record. Do not put serial numbers
or owner credentials in the public repository.
