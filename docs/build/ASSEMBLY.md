# Assembly

This is a high-level sequence for the C-F bench unit. It is not a mechanical
drawing. Where a dimension, pinout or torque value is not in the repository, the
step says so. Do not fill those gaps by guessing.

## 1. Inventory

Lay out every part and check it against the
[bill of materials](../../hardware/BILL_OF_MATERIALS.md). Photograph the labels
and contents. Record board revisions and serial numbers. Stop if a connector or
part does not match the documented interface.

## 2. Compute board and storage

Fit the heatsink and the NVMe drive to the Orange Pi 5 Plus. Use the vendor's
documented standoff and thermal path. The repository does not publish a torque
figure for the standoffs, so follow the hardware you received.

Do not power the board until the storage, cooler and any HAT are seated.

## 3. Panel and touch

The C-F bench build uses the 6.25-inch 720x1560 HDMI panel with a separate USB
touch interface. Mount the panel with strain relief on both cables. The final
G/H product path uses the ribbon DSI panel, and its adapter and pinout are still
open.

Check that the ribbon or HDMI connector is fully seated before applying power.
The panel is not an official final specification for the product.

## 4. Radio and GNSS

Fit the Waveshare SX1262 LoRaWAN/GNSS HAT. The L76K receiver's UART is UART6_M1
on header pins 8 and 10. UART2 is the debug console and must not be reused for
the receiver.

Fit the LoRa antenna before transmitting. Connect the GNSS antenna and place it
where it can see the sky. An antenna is not a fix: the receiver can report
`ANTENNA OPEN`, mode 1 and zero satellites while the service is healthy.

The radio's exact regional variant and the host protocol must be recorded per
unit. Do not assume the A/B Heltec image is interchangeable with the SX1262 HAT.

## 5. Audio

The Orange Pi 5 Plus build uses the platform ES8388 codec. Keep the microphone
and speaker wiring away from the radio and the panel ribbon.

Do not connect a speaker until the board revision, connector polarity and
speaker impedance are confirmed. The repository does not approve a generic
speaker for this build.

## 6. Camera

The C-F camera choice is the IMX415. The camera FPC and adapter path are still
pending, and the connector must be confirmed against the physical board. Do not
force a ribbon into a connector that has not been checked.

## 7. Power

The Orange Pi 5 Plus Type-C port is a fixed 5 V input. Use one of:

- a fixed 5 V/4 A USB-C supply;
- a 12 V or 20 V PD source with a 5 V/4 A buck converter;
- a bench supply at 5.1 V/5 A.

Do not connect a PD charger directly and expect negotiation. The G/H ROCK 5B+
units have a different power path and can use a common PD charger.

## 8. Cable dress and first power

Route the panel, camera, audio and antenna leads so they cannot be pinched by
the enclosure. Leave the storage device reachable.

For the first power-on:

1. Keep the unit on a non-conductive bench.
2. Connect Ethernet and the panel.
3. Apply power and watch for boot output.
4. Confirm the panel shows the portrait kiosk.
5. Stop at the first sign of a brownout, a hot component or a repeating reset.

The first boot and owner setup steps are in
[First boot](../boot/FIRST_BOOT.md).

## Related

- [Internal panel and camera research](../lantern/CF_INTERNAL_PANEL_CAMERA_RESEARCH.md)
- [Hardware and software contract](../lantern/HARDWARE_SOFTWARE_CONTRACT.md)
- [Bring-up and validation](../lantern/BRINGUP_AND_VALIDATION.md)
- [Device roles](../lantern/DEVICE_ROLES.md)
