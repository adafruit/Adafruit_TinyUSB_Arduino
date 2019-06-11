// The MIT License (MIT)
// Copyright (c) 2019 Ha Thach for Adafruit Industries

/* This example exposes both external flash and SD card as mass storage
 * using Adafruit_SPIFlash+Adafruit_QSPI and SdFat Library
 */

#include "SPI.h"
#include "SdFat.h"

#include "Adafruit_TinyUSB.h"
#include "Adafruit_SPIFlash.h"

const int chipSelect = 10;
const int spi_freq_mhz = 50;

#if defined(__SAMD51__) || defined(NRF52840_XXAA)
  #include "Adafruit_QSPI.h"
  #include "Adafruit_QSPI_Flash.h"

  Adafruit_QSPI_Flash flash;
#else
  // Configuration of the flash chip pins and flash fatfs object.
  // You don't normally need to change these if using a Feather/Metro
  // M0 express board.

  // Flash chip type. If you change this be sure to change the fatfs to match as well
  #define FLASH_TYPE     SPIFLASHTYPE_W25Q16BV

  #if (SPI_INTERFACES_COUNT == 1)
    #define FLASH_SS       SS                    // Flash chip SS pin.
    #define FLASH_SPI_PORT SPI                   // What SPI port is Flash on?
  #else
    #define FLASH_SS       SS1                    // Flash chip SS pin.
    #define FLASH_SPI_PORT SPI1                   // What SPI port is Flash on?
  #endif

  Adafruit_SPIFlash flash(FLASH_SS, &FLASH_SPI_PORT);     // Use hardware SPI
#endif

Adafruit_USBD_MSC usb_msc;
SdFat sd;

// the setup function runs once when you press reset or power the board
void setup()
{
  // MSC with 2 Logical Units
  usb_msc.setMaxLun(2);

  //------------- Lun 0 for external flash -------------//
#if defined(__SAMD51__) || defined(NRF52840_XXAA)
  flash.begin();
#else
  flash.begin(FLASH_TYPE);
#endif
  usb_msc.setID(0, "Adafruit", "External Flash", "1.0");
  usb_msc.setCapacity(0, flash.pageSize()*flash.numPages()/512, 512);
  usb_msc.setReadWriteCallback(0, external_flash_read_cb, external_flash_write_cb, external_flash_flush_cb);
  usb_msc.setUnitReady(0, true);

  //------------- Lun 1 for SD card -------------//
  usb_msc.setID(1, "Adafruit", "SD Card", "1.0");
  usb_msc.setReadWriteCallback(1, sdcard_read_cb, sdcard_write_cb, sdcard_flush_cb);

  if ( sd.cardBegin(chipSelect, SD_SCK_MHZ(spi_freq_mhz)) )
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
  const uint32_t addr = lba*512;
  flash_cache_read((uint8_t*) buffer, addr, bufsize);
  return bufsize;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and
// return number of written bytes (must be multiple of block size)
int32_t external_flash_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize)
{
  // need to erase & caching write back
  const uint32_t addr = lba*512;
  flash_cache_write(addr, buffer, bufsize);
  return bufsize;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void external_flash_flush_cb (void)
{
  flash_cache_flush();
}


//--------------------------------------------------------------------+
// Flash Caching for External Flash
//--------------------------------------------------------------------+
#define FLASH_CACHE_SIZE          4096        // must be a erasable page size
#define FLASH_CACHE_INVALID_ADDR  0xffffffff

uint32_t cache_addr = FLASH_CACHE_INVALID_ADDR;
uint8_t  cache_buf[FLASH_CACHE_SIZE];

static inline uint32_t page_addr_of (uint32_t addr)
{
  return addr & ~(FLASH_CACHE_SIZE - 1);
}

static inline uint32_t page_offset_of (uint32_t addr)
{
  return addr & (FLASH_CACHE_SIZE - 1);
}

void flash_cache_flush (void)
{
  if ( cache_addr == FLASH_CACHE_INVALID_ADDR ) return;

  // indicator
  digitalWrite(LED_BUILTIN, HIGH);

  flash.eraseSector(cache_addr/FLASH_CACHE_SIZE);
  flash.writeBuffer(cache_addr, cache_buf, FLASH_CACHE_SIZE);

  digitalWrite(LED_BUILTIN, LOW);

  cache_addr = FLASH_CACHE_INVALID_ADDR;
}

uint32_t flash_cache_write (uint32_t dst, void const * src, uint32_t len)
{
  uint8_t const * src8 = (uint8_t const *) src;
  uint32_t remain = len;

  // Program up to page boundary each loop
  while ( remain )
  {
    uint32_t const page_addr = page_addr_of(dst);
    uint32_t const offset = page_offset_of(dst);

    uint32_t wr_bytes = FLASH_CACHE_SIZE - offset;
    wr_bytes = min(remain, wr_bytes);

    // Page changes, flush old and update new cache
    if ( page_addr != cache_addr )
    {
      flash_cache_flush();
      cache_addr = page_addr;

      // read a whole page from flash
      flash.readBuffer(page_addr, cache_buf, FLASH_CACHE_SIZE);
    }

    memcpy(cache_buf + offset, src8, wr_bytes);

    // adjust for next run
    src8 += wr_bytes;
    remain -= wr_bytes;
    dst += wr_bytes;
  }

  return len - remain;
}

void flash_cache_read (uint8_t* dst, uint32_t addr, uint32_t count)
{
  // overwrite with cache value if available
  if ( (cache_addr != FLASH_CACHE_INVALID_ADDR) &&
       !(addr < cache_addr && addr + count <= cache_addr) &&
       !(addr >= cache_addr + FLASH_CACHE_SIZE) )
  {
    int dst_off = cache_addr - addr;
    int src_off = 0;

    if ( dst_off < 0 )
    {
      src_off = -dst_off;
      dst_off = 0;
    }

    int cache_bytes = min(FLASH_CACHE_SIZE-src_off, count - dst_off);

    // start to cached
    if ( dst_off ) flash.readBuffer(addr, dst, dst_off);

    // cached
    memcpy(dst + dst_off, cache_buf + src_off, cache_bytes);

    // cached to end
    int copied = dst_off + cache_bytes;
    if ( copied < count ) flash.readBuffer(addr + copied, dst + copied, count - copied);
  }
  else
  {
    flash.readBuffer(addr, dst, count);
  }
}
