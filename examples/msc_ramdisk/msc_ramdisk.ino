#include "Adafruit_TinyUSB.h"
#include "ramdisk.h"

Adafruit_USBD_MSC usbmsc;

// the setup function runs once when you press reset or power the board
void setup()
{
  // block count and size are defined in variant.h
  usbmsc.setCapacity(0, DISK_BLOCK_NUM, DISK_BLOCK_SIZE);
  usbmsc.setCallback(0, ram_read_cb, ram_write_cb, ram_flush_cb);
  usbmsc.begin();

  Serial.begin(115200);
  while ( !Serial ) delay(10);   // wait for native usb

  Serial.println("Adafruit TinyUSB Mass Storage Disk RAM example");
}

void loop()
{
  // nothing to do
}

// Callback invoked when received READ10 command.
// Copy disk's data to buffer (up to bufsize) and 
// return number of copied bytes (must be multiple of block size) 
int32_t ram_read_cb (uint32_t lba, void* buffer, uint32_t bufsize)
{
  uint8_t const* addr = msc_disk[lba];
  memcpy(buffer, addr, bufsize);

  return bufsize;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and 
// return number of written bytes (must be multiple of block size)
int32_t ram_write_cb (uint32_t lba, uint8_t* buffer, uint32_t bufsize)
{
  uint8_t* addr = msc_disk[lba];
  memcpy(addr, buffer, bufsize);

  return bufsize;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void ram_flush_cb (void)
{
  // nothing to do
}
