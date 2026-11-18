/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above must be included in any redistribution
*********************************************************************/

// Expose the Metro CH32V407/467 microSD socket as a USB drive, with CDC serial
// echo alongside it. Requires SdFat - Adafruit Fork 2.3.103 or newer and the
// community CH32 core at commit d5cac2161c17e3aec14d0338fdc684b20c0ba517 or newer
// (SD-card busy fix: https://github.com/ch32-community/arduino_core_ch32/pull/156).
// Use a Boards Manager release containing that commit. For an unreleased fix,
// first install the core through Boards Manager to get its compiler/Python tools.
// Move its version directory under Arduino15/packages/ch32-community/hardware/
// ch32v/ aside as a backup, then clone the repository into its original location:
// https://github.com/ch32-community/arduino_core_ch32.git (use --recursive).
// Check out the commit above (or newer)
// and run git submodule update --init --recursive before restarting the IDE.
//
// Insert an already formatted card before resetting the board. The computer
// owns the filesystem: do not also mount it with FsVolume or open files here.
// Eject the drive on the computer before removing the card or entering TinyUF2.
// Reset after reinserting a card or to expose an ejected card again.
//
// Select 100 MHz for the widest card compatibility. At 200 MHz the slowest
// SDIO initialization clock is 778 kHz, above the SD specification's 400 kHz
// limit; some cards will fail to initialize. Data transfers use 10 MHz at both
// CPU speeds. This sketch never formats the card or changes the CPU clock.

#include <Adafruit_TinyUSB.h>
#include <CH32SDIO.h>

#if !defined(ARDUINO_ADAFRUIT_METRO_CH32V407) && !defined(ARDUINO_ADAFRUIT_METRO_CH32V467)
  #error Select an Adafruit Metro CH32V407 or CH32V467 in the community CH32 core.
#endif

CH32SDIO card;
Adafruit_USBD_MSC usb_msc;

const uint32_t blockSize = 512;
bool cardReady = false;
bool ejected = false;
bool serialConnected = false;

void setup() {
  // Start without waiting for Serial Monitor: the USB drive must work alone.
  Serial.begin(115200);
  pinMode(PIN_SD_DETECT, INPUT_PULLUP);

  usb_msc.setID("Adafruit", "Metro SDIO", "1.0");
  usb_msc.setReadWriteCallback(msc_read_cb, msc_write_cb, msc_flush_cb);
  usb_msc.setReadyCallback(msc_ready_cb);
  usb_msc.setStartStopCallback(msc_start_stop_cb);
  usb_msc.setUnitReady(false);
  if (!usb_msc.begin()) {
    return;
  }

  // The core starts USB before setup(). Include MSC if CDC enumerated first.
  if (TinyUSBDevice.mounted()) {
    TinyUSBDevice.detach();
    delay(10);
    TinyUSBDevice.attach();
  }

  if (digitalRead(PIN_SD_DETECT) == LOW && card.begin()) {
    usb_msc.setCapacity(card.sectorCount(), blockSize);
    cardReady = true;
    usb_msc.setUnitReady(true);
  }
}

void loop() {
  // The community core services TinyUSB in foreground code. No separate USB
  // task or interrupt-side SDIO access is needed in this example.
  if (Serial && !serialConnected) {
    delay(250); // Let the computer finish opening the serial port.
    Serial.print("Adafruit TinyUSB Metro SDIO mass storage\n\r");
    if (digitalRead(PIN_SD_DETECT) != LOW) {
      Serial.print("No card detected. Insert a card and reset.\n\r");
    } else if (ejected) {
      Serial.print("Card ejected. Reset to expose it again.\n\r");
    } else if (cardReady) {
      Serial.print("Card ready. Eject the USB drive before removing it.\n\r");
    } else if (card.error() == CH32SDIO::None) {
      Serial.print("Card not initialized. Reset with the card inserted.\n\r");
    } else {
      Serial.print("SDIO failed. Error: ");
      Serial.print(card.error());
      Serial.print(" command: ");
      Serial.print(card.lastCommand());
      Serial.print(" status: 0x");
      Serial.print(card.status(), HEX);
      Serial.print("\n\rReset to retry; use 100 MHz if initialization fails.\n\r");
    }
    Serial.print("USB serial echoes received bytes.\n\r");
  }
  serialConnected = (bool) Serial;
  while (Serial.available()) {
    Serial.write(Serial.read());
  }
}

bool msc_ready_cb() {
  if (digitalRead(PIN_SD_DETECT) != LOW) {
    cardReady = false;
  }
  return cardReady && !ejected;
}

int32_t msc_read_cb(uint32_t lba, void *buffer, uint32_t bufsize) {
  if (!msc_ready_cb()) {
    return -1;
  }
  if (bufsize % blockSize != 0) {
    return -1;
  }
  if (!card.readSectors(lba, (uint8_t *) buffer, bufsize / blockSize)) {
    // A failed SDIO transfer needs reinitialization. Keep the drive unavailable
    // until reset so subsequent operations cannot use an invalid controller.
    cardReady = false;
    usb_msc.setUnitReady(false);
    return -1;
  }
  return bufsize;
}

int32_t msc_write_cb(uint32_t lba, uint8_t *buffer, uint32_t bufsize) {
  if (!msc_ready_cb()) {
    return -1;
  }
  if (bufsize % blockSize != 0) {
    return -1;
  }
  if (!card.writeSectors(lba, buffer, bufsize / blockSize)) {
    cardReady = false;
    usb_msc.setUnitReady(false);
    return -1;
  }
  return bufsize;
}

void msc_flush_cb() {
  // No filesystem is mounted on the MCU, so there is no FsVolume cache to clear.
  if (cardReady && !card.syncDevice()) {
    cardReady = false;
    usb_msc.setUnitReady(false);
  }
}

bool msc_start_stop_cb(uint8_t power_condition, bool start, bool load_eject) {
  (void) power_condition;
  if (!load_eject) {
    return true;
  }
  if (!cardReady) {
    return false;
  }
  if (!start && !card.syncDevice()) {
    cardReady = false;
    usb_msc.setUnitReady(false);
    return false;
  }
  ejected = !start;
  usb_msc.setUnitReady(!ejected);
  return true;
}
