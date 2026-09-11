# SAGE Lantern Audio Bridge

ESP-IDF firmware for the Waveshare ESP32-S3-AUDIO-Board v1.1 in Lantern Unit A.
It presents the board to the Radxa as a fixed-format USB Audio Class microphone
and speaker (16 kHz, stereo, signed 16-bit PCM). The standard Linux ALSA stack
can therefore carry voice audio without Bluetooth or a bespoke serial protocol.

The bridge uses the board's ES7210 microphone ADC and ES8311 speaker DAC. It is
deliberately independent of the LoRa radio: MeshCore remains on its own USB
serial interface.

## Build

Use the pinned ESP-IDF 5.5.2 container from the repository root:

```sh
docker run --rm -v "$PWD:/work" -w /work/firmware/lantern-audio-bridge \
  espressif/idf:v5.5.2 idf.py set-target esp32s3 build
```

Do not flash a field board until its complete factory flash has been backed up
and BOOT/RESET recovery access has been confirmed. TinyUSB takes ownership of
the native USB data pins during normal operation; ROM download mode remains the
recovery path.
