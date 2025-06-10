/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

/* This example expose SD card as mass storage using
 * - SdFat https://github.com/adafruit/SdFat
 */

#include "SPI.h"
#include "SdFat_Adafruit_Fork.h"
#include "Adafruit_TinyUSB.h"

//--------------------------------------------------------------------+
// SDCard Config
//--------------------------------------------------------------------+
#if defined(ARDUINO_PYPORTAL_M4) || defined(ARDUINO_PYPORTAL_M4_TITANO)
  // PyPortal has on-board card reader
  #define SDCARD_CS            32
  #define SDCARD_DETECT        33
  #define SDCARD_DETECT_ACTIVE HIGH

#elif defined(ARDUINO_ADAFRUIT_METRO_RP2040)
  #define SDIO_CLK_PIN  18
  #define SDIO_CMD_PIN  19 // MOSI
  #define SDIO_DAT0_PIN 20 // DAT1: 21, DAT2: 22, DAT3: 23

  #define SDCARD_DETECT        15
  #define SDCARD_DETECT_ACTIVE LOW

#elif defined(ARDUINO_ADAFRUIT_METRO_RP2350)
  #define SDIO_CLK_PIN  34
  #define SDIO_CMD_PIN  35 // MOSI
  #define SDIO_DAT0_PIN 36 // DAT1: 37, DAT2: 38, DAT3: 39

  #define SDCARD_DETECT        40
  #define SDCARD_DETECT_ACTIVE LOW

#elif defined(ARDUINO_ADAFRUIT_FRUITJAM_RP2350)
  #define SDIO_CLK_PIN  34
  #define SDIO_CMD_PIN  35 // MOSI
  #define SDIO_DAT0_PIN 36 // DAT1: 37, DAT2: 38, DAT3: 39

  #define SDCARD_DETECT        33
  #define SDCARD_DETECT_ACTIVE LOW

#else
  // Use SPI, no detect
  #define SDCARD_CS       10
#endif

#if defined(SDIO_CLK_PIN) && defined(SDIO_CMD_PIN) && defined(SDIO_DAT0_PIN)
  #define SD_CONFIG SdioConfig(SDIO_CLK_PIN, SDIO_CMD_PIN, SDIO_DAT0_PIN)
#else
  #define SD_CONFIG SdSpiConfig(SDCARD_CS, SHARED_SPI, SD_SCK_MHZ(50))
#endif

// File system on SD Card
SdFat sd;

// USB Mass Storage object
Adafruit_USBD_MSC usb_msc;

// the setup function runs once when you press reset or power the board
void setup() {
#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
#endif
  Serial.begin(115200);

  // Set disk vendor id, product id and revision with string up to 8, 16, 4 characters respectively
  usb_msc.setID("Adafruit", "SD Card", "1.0");

  // Set read write callback
  usb_msc.setReadWriteCallback(msc_read_cb, msc_write_cb, msc_flush_cb);

  // Still initialize MSC but tell usb stack that MSC is not ready to read/write
  // If we don't initialize, board will be enumerated as CDC only
  usb_msc.setUnitReady(false);
  usb_msc.begin();

  // If already enumerated, additional class driverr begin() e.g msc, hid, midi won't take effect until re-enumeration
  if (TinyUSBDevice.mounted()) {
    TinyUSBDevice.detach();
    delay(10);
    TinyUSBDevice.attach();
  }

  //while ( !Serial ) delay(10);   // wait for native usb
  Serial.println("Adafruit TinyUSB Mass Storage SD Card example");
  Serial.print("\nInitializing SD card ... ");

  if (!sd.begin(SD_CONFIG)) {
    Serial.println("initialization failed. Things to check:");
    Serial.println("- is a card inserted?");
    Serial.println("- is your wiring correct?");
    Serial.println("- did you change the SDCARD_CS or SDIO pin to match your shield or module?");
    while (1) delay(1);
  }

  // Size in blocks (512 bytes)
  uint32_t block_count = sd.card()->sectorCount();
  Serial.print("Volume size (MB):  ");
  Serial.println((block_count/2) / 1024);

  // Set disk size, SD block size is always 512
  usb_msc.setCapacity(block_count, 512);

  // MSC is ready for read/write
  usb_msc.setUnitReady(true);
}

void loop() {
  // noting to do
}

// Callback invoked when received READ10 command.
// Copy disk's data to buffer (up to bufsize) and
// return number of copied bytes (must be multiple of block size)
int32_t msc_read_cb (uint32_t lba, void* buffer, uint32_t bufsize) {
  bool rc = sd.card()->readSectors(lba, (uint8_t*) buffer, bufsize/512);
  return rc ? bufsize : -1;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and 
// return number of written bytes (must be multiple of block size)
int32_t msc_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize) {
#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, HIGH);
#endif
  bool rc = sd.card()->writeSectors(lba, buffer, bufsize/512);
  return rc ? bufsize : -1;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void msc_flush_cb (void) {
  sd.card()->syncDevice();
  sd.cacheClear();   // clear file system's cache to force refresh

#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, LOW);
#endif
}
