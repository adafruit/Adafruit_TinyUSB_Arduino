#include "Adafruit_TinyUSB.h"
#include "ramdisk.h"

Adafruit_USBD_MSC usbmsc;

// the setup function runs once when you press reset or power the board
void setup()
{
  // block count and size are defined in variant.h
  usbmsc.setCapacity(DISK_BLOCK_NUM, DISK_BLOCK_SIZE);
  usbmsc.setCallback(ram_read_cb, ram_write_cb, ram_flush_cb);
  usbmsc.begin();

  Serial.begin(115200);
  while ( !Serial ) delay(10);   // wait for native usb

  Serial.println("Adafruit TinyUSB Mass Storage Disk RAM example");
}

void loop()
{
  // nothing to do
}

int32_t ram_read_cb (uint8_t lun, uint32_t lba, uint32_t offset, void* buffer, uint32_t bufsize)
{
  (void) lun;
  uint8_t const* addr = msc_disk[lba] + offset;
  memcpy(buffer, addr, bufsize);

  return bufsize;
}

int32_t ram_write_cb (uint8_t lun, uint32_t lba, uint32_t offset, uint8_t* buffer, uint32_t bufsize)
{
  (void) lun;

  uint8_t* addr = msc_disk[lba] + offset;
  memcpy(addr, buffer, bufsize);

  return bufsize;
}

void ram_flush_cb (uint8_t lun)
{
  (void) lun;
  // nothing to do
}
