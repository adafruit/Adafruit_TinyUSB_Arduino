/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

/* This example exposes both external flash and SD card as mass storage (dual LUNs)
 * Following library is required
 *   - Adafruit_SPIFlash https://github.com/adafruit/Adafruit_SPIFlash
 *   - SdFat https://github.com/adafruit/SdFat
 *
 * Note: Adafruit fork of SdFat enabled ENABLE_EXTENDED_TRANSFER_CLASS and FAT12_SUPPORT
 * in SdFatConfig.h, which is needed to run SdFat on external flash. You can use original
 * SdFat library and manually change those macros
 *
 * Note2: If your flash is not formatted as FAT12 previously, you could format it using
 * follow sketch https://github.com/adafruit/Adafruit_SPIFlash/tree/master/examples/SdFat_format
 */

#include "SPI.h"
#include "SdFat_Adafruit_Fork.h"
#include "Adafruit_SPIFlash.h"
#include "Adafruit_TinyUSB.h"

//--------------------------------------------------------------------+
// External Flash Config
//--------------------------------------------------------------------+

// for flashTransport definition
#include "flash_config.h"
Adafruit_SPIFlash flash(&flashTransport);

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
  // Note: not working yet (need troubleshoot later)
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

// Set to true when PC write to flash
bool sd_inited = false;

// the setup function runs once when you press reset or power the board
void setup() {
#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
#endif
  Serial.begin(115200);

  // MSC with 2 Logical Units: LUN0: External Flash, LUN1: SDCard
  usb_msc.setMaxLun(2);

  usb_msc.setID(0, "Adafruit", "External Flash", "1.0");
  usb_msc.setID(1, "Adafruit", "SD Card", "1.0");

  // Since initialize both external flash and SD card can take time.
  // If it takes too long, our board could be enumerated as CDC device only
  // i.e without Mass Storage. To prevent this, we call Mass Storage begin first
  // LUN readiness will always be set later on
  usb_msc.begin();

  // If already enumerated, additional class driverr begin() e.g msc, hid, midi won't take effect until re-enumeration
  if (TinyUSBDevice.mounted()) {
    TinyUSBDevice.detach();
    delay(10);
    TinyUSBDevice.attach();
  }

  //------------- Lun 0 for external flash -------------//
  flash.begin();

  usb_msc.setCapacity(0, flash.size()/512, 512);
  usb_msc.setReadWriteCallback(0, external_flash_read_cb, external_flash_write_cb, external_flash_flush_cb);
  usb_msc.setUnitReady(0, true);

  //------------- Lun 1 for SD card -------------//
#ifdef SDCARD_DETECT
  // DETECT pin is available, detect card present on the fly with test unit ready
  pinMode(SDCARD_DETECT, INPUT);
  usb_msc.setReadyCallback(1, sdcard_ready_callback);
#else
  // no DETECT pin, card must be inserted when powered on
  init_sdcard();
  sd_inited = true;
  usb_msc.setUnitReady(1, true);
#endif

  // while ( !Serial ) delay(10);   // wait for native usb
  Serial.println("Adafruit TinyUSB Mass Storage External Flash + SD Card example");
  delay(1000);
}

bool init_sdcard(void) {
  Serial.print("Init SDCard ... ");

  if (!sd.begin(SD_CONFIG)) {
    Serial.println("initialization failed. Things to check:");
    Serial.println("- is a card inserted?");
    Serial.println("- is your wiring correct?");
    Serial.println("- did you change the SDCARD_CS or SDIO pin to match your shield or module?");
    return false;
  }

  uint32_t block_count = sd.card()->sectorCount();
  usb_msc.setCapacity(1, block_count, 512);
  usb_msc.setReadWriteCallback(1, sdcard_read_cb, sdcard_write_cb, sdcard_flush_cb);

  Serial.print("OK, Card size = ");
  Serial.print((block_count / (1024 * 1024)) * 512);
  Serial.println(" MB");

  return true;
}

void print_rootdir(File32* rdir) {
  File32 file;

  // Open next file in root.
  // Warning, openNext starts at the current directory position
  // so a rewind of the directory may be required.
  while (file.openNext(rdir, O_RDONLY)) {
    file.printFileSize(&Serial);
    Serial.write(' ');
    file.printName(&Serial);
    if (file.isDir()) {
      // Indicate a directory.
      Serial.write('/');
    }
    Serial.println();
    file.close();
  }
}

void loop() {
  // nothing to do
}


//--------------------------------------------------------------------+
// SD Card callbacks
//--------------------------------------------------------------------+

int32_t sdcard_read_cb (uint32_t lba, void* buffer, uint32_t bufsize) {
  bool rc = sd.card()->readSectors(lba, (uint8_t*) buffer, bufsize/512);
  return rc ? bufsize : -1;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and 
// return number of written bytes (must be multiple of block size)
int32_t sdcard_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize) {
#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, HIGH);
#endif
  bool rc = sd.card()->writeSectors(lba, buffer, bufsize/512);
  return rc ? bufsize : -1;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void sdcard_flush_cb (void) {
  sd.card()->syncDevice();
  sd.cacheClear(); // clear file system's cache to force refresh

#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, LOW);
#endif
}

#ifdef SDCARD_DETECT
// Invoked when received Test Unit Ready command.
// return true allowing host to read/write this LUN e.g SD card inserted
bool sdcard_ready_callback(void) {
  // Card is inserted
  if (digitalRead(SDCARD_DETECT) == SDCARD_DETECT_ACTIVE) {
    // init SD card if not already
    if (!sd_inited) {
      sd_inited = init_sdcard();
    }
  } else {
    sd_inited = false;
    usb_msc.setReadWriteCallback(1, NULL, NULL, NULL);
  }

  return sd_inited;
}
#endif

//--------------------------------------------------------------------+
// External Flash callbacks
//--------------------------------------------------------------------+

// Callback invoked when received READ10 command.
// Copy disk's data to buffer (up to bufsize) and
// return number of copied bytes (must be multiple of block size)
int32_t external_flash_read_cb (uint32_t lba, void* buffer, uint32_t bufsize) {
  // Note: SPIFLash Bock API: readBlocks/writeBlocks/syncBlocks
  // already include 4K sector caching internally. We don't need to cache it, yahhhh!!
  return flash.readBlocks(lba, (uint8_t*) buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and
// return number of written bytes (must be multiple of block size)
int32_t external_flash_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize) {
#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, HIGH);
#endif

  // Note: SPIFLash Bock API: readBlocks/writeBlocks/syncBlocks
  // already include 4K sector caching internally. We don't need to cache it, yahhhh!!
  return flash.writeBlocks(lba, buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void external_flash_flush_cb (void) {
  flash.syncBlocks();
#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, LOW);
#endif
}
