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
  #define SDCARD_CS            23
  #define SDCARD_DETECT        15
  #define SDCARD_DETECT_ACTIVE LOW
#else
  #define SDCARD_CS       10
  // no detect
#endif

// File system on SD Card
SdFat sd;

SdFile root;
SdFile file;

// USB Mass Storage object
Adafruit_USBD_MSC usb_msc;

// Set to true when PC write to flash
bool fs_changed;

// the setup function runs once when you press reset or power the board
void setup() {
  Serial.begin(115200);

#ifdef LED_BUILTIN
  pinMode(LED_BUILTIN, OUTPUT);
#endif

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

  while ( !Serial ) delay(10);   // wait for native usb
  Serial.println("Adafruit TinyUSB Mass Storage SD Card example");
  Serial.print("\nInitializing SD card ... ");
  Serial.print("CS = "); Serial.println(SDCARD_CS);

  if ( !sd.begin(SDCARD_CS, SD_SCK_MHZ(50)) ) {
    Serial.println("initialization failed. Things to check:");
    Serial.println("* is a card inserted?");
    Serial.println("* is your wiring correct?");
    Serial.println("* did you change the SDCARD_CS pin to match your shield or module?");
    while (1) delay(1);
  }

  // Size in blocks (512 bytes)
#if SD_FAT_VERSION >= 20000
  uint32_t block_count = sd.card()->sectorCount();
#else
  uint32_t block_count = sd.card()->cardSize();
#endif

  Serial.print("Volume size (MB):  ");
  Serial.println((block_count/2) / 1024);

  // Set disk size, SD block size is always 512
  usb_msc.setCapacity(block_count, 512);

  // MSC is ready for read/write
  usb_msc.setUnitReady(true);

  fs_changed = true; // to print contents initially
}

void loop() {
  if ( fs_changed ) {
    root.open("/");
    Serial.println("SD contents:");

    // Open next file in root.
    // Warning, openNext starts at the current directory position
    // so a rewind of the directory may be required.
    while ( file.openNext(&root, O_RDONLY) ) {
      file.printFileSize(&Serial);
      Serial.write(' ');
      file.printName(&Serial);
      if ( file.isDir() ) {
        // Indicate a directory.
        Serial.write('/');
      }
      Serial.println();
      file.close();
    }

    root.close();

    Serial.println();

    fs_changed = false;
    delay(1000); // refresh every 0.5 second
  }
}

// Callback invoked when received READ10 command.
// Copy disk's data to buffer (up to bufsize) and
// return number of copied bytes (must be multiple of block size)
int32_t msc_read_cb (uint32_t lba, void* buffer, uint32_t bufsize) {
  bool rc;

#if SD_FAT_VERSION >= 20000
  rc = sd.card()->readSectors(lba, (uint8_t*) buffer, bufsize/512);
#else
  rc = sd.card()->readBlocks(lba, (uint8_t*) buffer, bufsize/512);
#endif

  return rc ? bufsize : -1;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and 
// return number of written bytes (must be multiple of block size)
int32_t msc_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize) {
  bool rc;

#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, HIGH);
#endif

#if SD_FAT_VERSION >= 20000
  rc = sd.card()->writeSectors(lba, buffer, bufsize/512);
#else
  rc = sd.card()->writeBlocks(lba, buffer, bufsize/512);
#endif

  return rc ? bufsize : -1;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void msc_flush_cb (void) {
#if SD_FAT_VERSION >= 20000
  sd.card()->syncDevice();
#else
  sd.card()->syncBlocks();
#endif

  // clear file system's cache to force refresh
  sd.cacheClear();

  fs_changed = true;

#ifdef LED_BUILTIN
  digitalWrite(LED_BUILTIN, LOW);
#endif
}
