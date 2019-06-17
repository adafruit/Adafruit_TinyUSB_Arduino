// The MIT License (MIT)
// Copyright (c) 2019 Ha Thach for Adafruit Industries

/* This example exposes both external flash and SD card as mass storage
 * using Adafruit_SPIFlash and SdFat Library
 *   - SdFat https://github.com/greiman/SdFat
 *   - Adafruit_SPIFlash https://github.com/adafruit/Adafruit_SPIFlash
 */

#include "SPI.h"
#include "SdFat.h"
#include "Adafruit_SPIFlash.h"
#include "Adafruit_TinyUSB.h"

const int chipSelect = 10;

#if defined(__SAMD51__) || defined(NRF52840_XXAA)
  Adafruit_FlashTransport_QSPI flashTransport(PIN_QSPI_SCK, PIN_QSPI_CS, PIN_QSPI_IO0, PIN_QSPI_IO1, PIN_QSPI_IO2, PIN_QSPI_IO3);
#else
  #if (SPI_INTERFACES_COUNT == 1)
    Adafruit_FlashTransport_SPI flashTransport(SS, &SPI);
  #else
    Adafruit_FlashTransport_SPI flashTransport(SS1, &SPI1);
  #endif
#endif

Adafruit_SPIFlash flash(&flashTransport);

Adafruit_USBD_MSC usb_msc;
SdFat sd;

// the setup function runs once when you press reset or power the board
void setup()
{
  // MSC with 2 Logical Units
  usb_msc.setMaxLun(2);

  //------------- Lun 0 for external flash -------------//
  flash.begin();
  usb_msc.setID(0, "Adafruit", "External Flash", "1.0");
  usb_msc.setCapacity(0, flash.pageSize()*flash.numPages()/512, 512);
  usb_msc.setReadWriteCallback(0, external_flash_read_cb, external_flash_write_cb, external_flash_flush_cb);
  usb_msc.setUnitReady(0, true);

  //------------- Lun 1 for SD card -------------//
  usb_msc.setID(1, "Adafruit", "SD Card", "1.0");
  usb_msc.setReadWriteCallback(1, sdcard_read_cb, sdcard_write_cb, sdcard_flush_cb);

  if ( sd.cardBegin(chipSelect, SD_SCK_MHZ(50)) )
  {
    uint32_t block_count = sd.card()->cardSize();
    usb_msc.setCapacity(1, block_count, 512);
    usb_msc.setUnitReady(1, true);
  }

  usb_msc.begin();

  Serial.begin(115200);
  while ( !Serial ) delay(10);   // wait for native usb

  Serial.println("Adafruit TinyUSB Mass Storage External Flash + SD Card example");
}

void loop()
{
  // nothing to do
}


//--------------------------------------------------------------------+
// SD Card callbacks
//--------------------------------------------------------------------+

int32_t sdcard_read_cb (uint32_t lba, void* buffer, uint32_t bufsize)
{
  (void) bufsize;
  return sd.card()->readBlocks(lba, (uint8_t*) buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and 
// return number of written bytes (must be multiple of block size)
int32_t sdcard_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize)
{
  return sd.card()->writeBlocks(lba, buffer, bufsize/512) ? bufsize : -1;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void sdcard_flush_cb (void)
{
  sd.card()->syncBlocks();
}

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
  flash.readBlocks(lba, (uint8_t*) buffer, bufsize/512);
  return bufsize;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and
// return number of written bytes (must be multiple of block size)
int32_t external_flash_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize)
{
  // Note: SPIFLash Bock API: readBlocks/writeBlocks/syncBlocks
  // already include 4K sector caching internally. We don't need to cache it, yahhhh!!
  flash.writeBlocks(lba, buffer, bufsize/512);
  return bufsize;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void external_flash_flush_cb (void)
{
  flash.syncBlocks();
}
