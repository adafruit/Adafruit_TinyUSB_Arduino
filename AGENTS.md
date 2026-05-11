# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

Adafruit TinyUSB Arduino — an Arduino library wrapping the [TinyUSB](https://github.com/hathach/tinyusb) USB stack. Provides Arduino-friendly classes for USB device and host functionality across multiple MCU families (nRF52, SAMD, ESP32, RP2040, CH32, STM32).

## Build Commands

This is an Arduino library — there is no standalone build. It compiles as part of Arduino sketches via `arduino-cli`.

**Install library dependencies:**
```bash
arduino-cli lib install "Adafruit SPIFlash" "Adafruit seesaw Library" "Adafruit NeoPixel" "Adafruit Circuit Playground" "Adafruit InternalFlash" "SdFat - Adafruit Fork" "SD" "MIDI Library" "Pico PIO USB"
```

**Build a single sketch** (one representative board per platform):
```bash
# nRF52840
arduino-cli compile --warnings all --fqbn adafruit:nrf52:feather52840:softdevice=s140v6,debug=l0 path/to/sketch

# SAMD21 (M0)
arduino-cli compile --warnings all --fqbn adafruit:samd:adafruit_metro_m0:usbstack=tinyusb path/to/sketch

# SAMD51 (M4)
arduino-cli compile --warnings all --fqbn adafruit:samd:adafruit_metro_m4:usbstack=tinyusb path/to/sketch

# RP2040
arduino-cli compile --warnings all --fqbn rp2040:rp2040:adafruit_feather:usbstack=tinyusb path/to/sketch

# ESP32-S3
arduino-cli compile --warnings all --fqbn esp32:esp32:adafruit_feather_esp32s3 path/to/sketch

# ESP32-P4
arduino-cli compile --warnings all --fqbn espressif:esp32:esp32p4 path/to/sketch

# CH32V20x
arduino-cli compile --warnings all --fqbn WCH:ch32v:CH32V20x_EVT path/to/sketch
```

**CI builds** use [adafruit/ci-arduino](https://github.com/adafruit/ci-arduino) scripts:
```bash
# Build for a specific platform (requires ci-arduino checked out to ./ci)
python3 ci/build_platform.py <platform>
```

**CI platform matrix**: `feather_esp32_v2`, `feather_esp32s2`, `feather_esp32s3`, `esp32p4`, `cpb`, `nrf52840`, `feather_rp2040_tinyusb`, `pico_rp2040_tinyusb_host`, `metro_m0_tinyusb`, `metro_m4_tinyusb`, `CH32V20x_EVT`

**PlatformIO**: `platformio.ini` has environment definitions for all supported boards. Build with `pio run -e <env>`.

## Code Quality

**Pre-commit hooks** (`.pre-commit-config.yaml`):
```bash
pre-commit run --all-files
```
- **clang-format** (v15): applies only to `src/arduino/` code. The vendored TinyUSB core directories (`src/class/`, `src/common/`, `src/device/`, `src/host/`, `src/osal/`, `src/portable/`, `src/tusb.c`, `src/tusb.h`, `src/tusb_option.h`) are excluded.
- **codespell**: spell checking with ignore list in `.codespell/`

## Architecture

### Layer Diagram
```
User Sketch  →  Adafruit_TinyUSB.h
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   USBD_Device   USBD_* classes  USBH_Host
   (descriptor    (CDC, HID,     (host mode
    management)    MSC, MIDI,     manager)
                   Video, WebUSB)
        │             │             │
        ▼             ▼             ▼
      TinyUSB Stack (tusb.h / tusb.c)
                      │
                      ▼
         Port Layer (src/arduino/ports/<mcu>/)
                      │
                      ▼
         Portable Drivers (src/portable/<vendor>/)
```

### Key Source Directories

- **`src/arduino/`** — Arduino abstraction layer (the code this project maintains)
  - `Adafruit_USBD_Device.*` — USB device manager, descriptor building, endpoint allocation
  - `Adafruit_USBD_CDC.*` — CDC serial (replaces Arduino `Serial` on supported cores)
  - `Adafruit_USBD_Interface.*` — Base class for all USB interfaces
  - `Adafruit_USBH_Host.*` — USB host stack manager
  - `hid/`, `msc/`, `midi/`, `video/`, `webusb/`, `cdc/` — class-specific wrappers
  - `ports/` — MCU-specific port implementations (nrf, samd, esp32, rp2040, ch32, stm32)

- **`src/class/`, `src/device/`, `src/host/`, `src/common/`, `src/portable/`, `src/osal/`** — Vendored TinyUSB core. Do NOT apply clang-format to these.

### Updating Vendored TinyUSB Core

When syncing `src/` from upstream [hathach/tinyusb](https://github.com/hathach/tinyusb), most files are copied as-is. However, **`src/tusb_option.h`** has an Arduino-specific patch that must be preserved. After copying the upstream version, re-add the following block (around line 265, inside the MCU detection `#elif` chain):

```c
#elif defined(ARDUINO_ARCH_ESP32)
  // ESP32 out-of-sync
  #include "arduino/ports/esp32/tusb_config_esp32.h"
```

This is needed because the ESP32 Arduino core bundles an older TinyUSB version, so the config must be force-included here rather than relying on the core's copy.

### Port Implementation Contract

Each MCU port in `src/arduino/ports/<mcu>/` must implement:
- `TinyUSB_Port_InitDevice()` — initialize USB hardware
- `TinyUSB_Port_EnterDFU()` — enter bootloader (1200 baud touch)
- `TinyUSB_Port_GetSerialNumber()` — return unique device serial

### Core Integration Modes

- **Built-in support** (nRF52, SAMD, RP2040, ESP32, CH32): the Arduino core calls `TinyUSB_Device_Init()`, `TinyUSB_Device_Task()`, `TinyUSB_Device_FlushCDC()` automatically. Macro `USE_TINYUSB` is defined.
- **Non-built-in** (STM32, mbed_rp2040): sketches must call these functions explicitly. Macro `TINYUSB_NEED_POLLING_TASK` is set.

### Configuration

- `src/tusb_config.h` — dispatches to per-MCU config (`tusb_config_<mcu>.h`)
- Per-class enable macros: `CFG_TUD_CDC`, `CFG_TUD_HID`, `CFG_TUD_MSC`, etc.
- `CFG_TUD_ENABLED` / `CFG_TUH_ENABLED` — device/host stack toggles

### Examples

40+ examples organized by USB class in `examples/`:
`CDC/`, `HID/`, `MassStorage/`, `MIDI/`, `WebUSB/`, `Video/`, `Vendor/`, `Composite/`, `DualRole/`, `Host/`

Board-specific test filtering uses `.test.only` files within example directories.
