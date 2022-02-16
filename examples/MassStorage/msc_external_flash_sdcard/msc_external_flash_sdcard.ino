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
 */

#include "SPI.h"
#include "SdFat.h"
#include "Adafruit_SPIFlash.h"
#include "Adafruit_TinyUSB.h"

//--------------------------------------------------------------------+
// External Flash Config
//--------------------------------------------------------------------+

// Un-comment to run example with custom SPI SPI and SS e.g with FRAM breakout
// #define CUSTOM_CS   A5
// #define CUSTOM_SPI  SPI

#if defined(CUSTOM_CS) && defined(CUSTOM_SPI)
  Adafruit_FlashTransport_SPI flashTransport(CUSTOM_CS, CUSTOM_SPI);

#elif defined(ARDUINO_ARCH_ESP32)
  // ESP32 use same flash device that store code.
  // Therefore there is no need to specify the SPI and SS
  Adafruit_FlashTransport_ESP32 flashTransport;

#elif defined(ARDUINO_ARCH_RP2040)
  // RP2040 use same flash device that store code.
  // Therefore there is no need to specify the SPI and SS
  // Use default (no-args) constructor to be compatible with CircuitPython partition scheme
  Adafruit_FlashTransport_RP2040 flashTransport;

  // For generic usage: Adafruit_FlashTransport_RP2040(start_address, size)
  // If start_address and size are both 0, value that match filesystem setting in
  // 'Tools->Flash Size' menu selection will be used

#else
  // On-board external flash (QSPI or SPI) macros should already
  // defined in your board variant if supported
  // - EXTERNAL_FLASH_USE_QSPI
  // - EXTERNAL_FLASH_USE_CS/EXTERNAL_FLASH_USE_SPI
  #if defined(EXTERNAL_FLASH_USE_QSPI)
    Adafruit_FlashTransport_QSPI flashTransport;

  #elif defined(EXTERNAL_FLASH_USE_SPI)
    Adafruit_FlashTransport_SPI flashTransport(EXTERNAL_FLASH_USE_CS, EXTERNAL_FLASH_USE_SPI);

  #else
    #error No QSPI/SPI flash are defined on your board variant.h !
  #endif
#endif


Adafruit_SPIFlash flash(&flashTransport);

// External Flash File system
FatFileSystem fatfs;

//--------------------------------------------------------------------+
// SDCard Config
//--------------------------------------------------------------------+

#if defined(ARDUINO_PYPORTAL_M4) || defined(ARDUINO_PYPORTAL_M4_TITANO)
  // PyPortal has on-board card reader
  #define SDCARD_CS       32
  #define SDCARD_DETECT   33
#else
  #define SDCARD_CS       10
  // no detect
#endif

// SDCard File system
SdFat sd;

// USB Mass Storage object
Adafruit_USBD_MSC usb_msc;

// Set to true when PC write to flash
bool sd_changed = false;
bool sd_inited = false;

bool flash_formatted = false;
bool flash_changed = false;

// the setup function runs once when you press reset or power the board
void setup()
{
  pinMode(LED_BUILTIN, OUTPUT);
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

  //------------- Lun 0 for external flash -------------//
  flash.begin();
  flash_formatted = fatfs.begin(&flash);

  usb_msc.setCapacity(0, flash.size()/512, 512);
  usb_msc.setReadWriteCallback(0, external_flash_read_cb, external_flash_write_cb, external_flash_flush_cb);
  usb_msc.setUnitReady(0, true);

  flash_changed = true; // to print contents initially

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

//  while ( !Serial ) delay(10);   // wait for native usb
  Serial.println("Adafruit TinyUSB Mass Storage External Flash + SD Card example");
  delay(1000);
}

bool init_sdcard(void)
{
  Serial.print("Init SDCard ... ");

  if ( !sd.begin(SDCARD_CS, SD_SCK_MHZ(50)) )
  {
    Serial.print("Failed ");
    sd.errorPrint();

    return false;
  }

  uint32_t block_count = sd.card()->cardSize();
  usb_msc.setCapacity(1, block_count, 512);
  usb_msc.setReadWriteCallback(1, sdcard_read_cb, sdcard_write_cb, sdcard_flush_cb);

  sd_changed = true; // to print contents initially

  Serial.print("OK, Card size = ");
  Serial.print((block_count / (1024*1024)) * 512);
  Serial.println(" MB");

  return true;
}

void print_rootdir(File* rdir)
{
  File file;

  // Open next file in root.
  // Warning, openNext starts at the current directory position
  // so a rewind of the directory may be required.
  while ( file.openNext(rdir, O_RDONLY) )
  {
    file.printFileSize(&Serial);
    Serial.write(' ');
    file.printName(&Serial);
    if ( file.isDir() )
    {
      // Indicate a directory.
      Serial.write('/');
    }
    Serial.println();
    file.close();
  }
}

void loop()
{
  if ( flash_changed )
  {
    if (!flash_formatted)
    {
      flash_formatted = fatfs.begin(&flash);
    }

    // skip if still not formatted
    if (flash_formatted)
    {
      File root;
      root = fatfs.open("/");

      Serial.println("Flash contents:");
      print_rootdir(&root);
      Serial.println();

      root.close();
    }

    flash_changed = false;
  }

  if ( sd_changed )
  {
    File root;
    root = sd.open("/");

    Serial.println("SD contents:");
    print_rootdir(&root);
    Serial.println();

    root.close();

    sd_changed = false;
  }

  delay(1000); // refresh every 1 second
}


//--------------------------------------------------------------------+
// SD Card callbacks
//--------------------------------------------------------------------+

int32_t sdcard_read_cb (uint32_t lba, void* buffer, uint32_t bufsize)
{
  return sd.card()->readBlocks(lba, (uint8_t*) buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and 
// return number of written bytes (must be multiple of block size)
int32_t sdcard_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize)
{
  digitalWrite(LED_BUILTIN, HIGH);

  return sd.card()->writeBlocks(lba, buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void sdcard_flush_cb (void)
{
  sd.card()->syncBlocks();

  // clear file system's cache to force refresh
  sd.cacheClear();

  sd_changed = true;

  digitalWrite(LED_BUILTIN, LOW);
}

#ifdef SDCARD_DETECT
// Invoked when received Test Unit Ready command.
// return true allowing host to read/write this LUN e.g SD card inserted
bool sdcard_ready_callback(void)
{
  // Card is inserted
  if ( digitalRead(SDCARD_DETECT) == HIGH )
  {
    // init SD card if not already
    if ( !sd_inited )
    {
      sd_inited = init_sdcard();
    }
  }else
  {
    sd_inited = false;
    usb_msc.setReadWriteCallback(1, NULL, NULL, NULL);
  }

  Serial.println(sd_inited);

  return sd_inited;
}
#endif

//--------------------------------------------------------------------+
// External Flash callbacks
//--------------------------------------------------------------------+

// Callback invoked when received READ10 command.
// Copy disk's data to buffer (up to bufsize) and
// return number of copied bytes (must be multiple of block size)
int32_t external_flash_read_cb (uint32_t lba, void* buffer, uint32_t bufsize)
{
  // Note: SPIFLash Bock API: readBlocks/writeBlocks/syncBlocks
  // already include 4K sector caching internally. We don't need to cache it, yahhhh!!
  return flash.readBlocks(lba, (uint8_t*) buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and
// return number of written bytes (must be multiple of block size)
int32_t external_flash_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize)
{
  digitalWrite(LED_BUILTIN, HIGH);

  // Note: SPIFLash Bock API: readBlocks/writeBlocks/syncBlocks
  // already include 4K sector caching internally. We don't need to cache it, yahhhh!!
  return flash.writeBlocks(lba, buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void external_flash_flush_cb (void)
{
  flash.syncBlocks();

  // clear file system's cache to force refresh
  fatfs.cacheClear();

  flash_changed = true;

  digitalWrite(LED_BUILTIN, LOW);
}
