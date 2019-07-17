# Adafruit TinyUSB Library for Arduino

[![Build Status](https://travis-ci.com/adafruit/Adafruit_TinyUSB_Arduino.svg?branch=master)](https://travis-ci.com/adafruit/Adafruit_TinyUSB_Arduino)[![License](https://img.shields.io/badge/license-MIT-brightgreen.svg)](https://opensource.org/licenses/MIT)

[TinyUSB](https://github.com/hathach/tinyusb) library for Arduino IDE. This library works with core platform that includes TinyUSB stack, typically you will see **Adafruit_TinyUSB_Core** inside core folder. Supported platform are:

- [Adafruit_nRF52_Arduino](https://github.com/adafruit/Adafruit_nRF52_Arduino)
- [Adafruit ArduinoCore-samd](https://github.com/adafruit/ArduinoCore-samd) **TinyUSB** must be selected in menu `Tools->USB Stack`

In addition to CDC that provided by the platform core, this library provide platform-independent for

- Human Interface Device (HID): Generic (In & Out), Keyboard, Mouse, Gamepad etc ...
- Mass Storage Class (MSC): with multiple LUNs
- Musical Instrument Digital Interface (MIDI)

More document to write ... 
