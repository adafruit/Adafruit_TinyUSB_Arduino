/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!
 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/
 /*
  ramdisk-3-files.ino
  Ram disk which expose 3 files
  - README.TXT  / ReadOnly 
  - WRITEME.TXT
  - VIEWME.JPG  / ReadOnly 
  Example written by Frederic Torres - 2023/02
  */

#include "Adafruit_TinyUSB.h"

// Sector Count: 16
// 16*512b == 8Kb of allocated space. In ramdisk.h file, the disk is set to 128 Kb of total space
#define DISK_BLOCK_NUM (1 /* BootSector*/ +2 /* Fatlinkedlist*/ +1 /* RootDirectory*/  +1/*readme.txt*/  +1/* writeme.txt*/ +10/*viewme.jpg*/ )

#define DISK_BLOCK_SIZE 512
#include "ramdisk.h"

bool _authorizedMode = true;
Adafruit_USBD_MSC usb_msc;

/*
// Eject button to demonstrate medium is not ready e.g SDCard is not present
// whenever this button is pressed and hold, it will report to host as not ready
#if defined(ARDUINO_SAMD_CIRCUITPLAYGROUND_EXPRESS) || defined(ARDUINO_NRF52840_CIRCUITPLAY)

  #define BTN_EJECT   4   // Left Button
  bool activeState = true;

#elif defined(ARDUINO_FUNHOUSE_ESP32S2)
  #define BTN_EJECT   BUTTON_DOWN
  bool activeState = true;

#elif defined PIN_BUTTON1
  #define BTN_EJECT   PIN_BUTTON1
  bool activeState = false;
#endif
*/

// the setup function runs once when you press reset or power the board
void setup()
{ 
  #if defined(ARDUINO_ARCH_MBED) && defined(ARDUINO_ARCH_RP2040)
    // Manual begin() is required on core without built-in support for TinyUSB such as - mbed rp2040
    TinyUSB_Device_Init(0);
  #endif
  
  usb_msc.setID("fDrive", "Mass Storage", "1.0"); // Set disk vendor id, product id and revision with string up to 8, 16, 4 characters respectively
  usb_msc.setCapacity(DISK_BLOCK_NUM, DISK_BLOCK_SIZE); // Set disk size
  usb_msc.setReadWriteCallback(msc_read_callback, msc_write_callback, msc_flush_callback); // Set callback
  usb_msc.setUnitReady(true);   // Set Lun ready (RAM disk is always ready)

#ifdef BTN_EJECT
  pinMode(BTN_EJECT, activeState ? INPUT_PULLDOWN : INPUT_PULLUP);
  usb_msc.setReadyCallback(msc_ready_callback);
#endif

  usb_msc.begin();

  Serial.begin(115200); // has to be executed after the usb initializaition
  Serial.println("Setup done");
}

void loop()
{
  
}

// Callback invoked when received READ10 command.
// Copy disk's data to buffer (up to bufsize) and
// return number of copied bytes (must be multiple of block size)
int32_t msc_read_callback (uint32_t lba, void* buffer, uint32_t bufsize)
{
  Serial.print("msc_read_callback lba:");
  Serial.print(lba);
  Serial.print(", bufsize:");
  Serial.println(bufsize);

  uint8_t const * addr = msc_disk[lba];
  memcpy(buffer, addr, bufsize);

  return bufsize;
}

// Callback invoked when received WRITE10 command.
// Process data in buffer to disk's storage and
// return number of written bytes (must be multiple of block size)
int32_t msc_write_callback (uint32_t lba, uint8_t* buffer, uint32_t bufsize)
{
  uint8_t* addr = msc_disk[lba];
  memcpy(addr, buffer, bufsize);

  return bufsize;
}

// Callback invoked when WRITE10 command is completed (status received and accepted by host).
// used to flush any pending cache.
void msc_flush_callback (void)
{
  // nothing to do
}


#ifdef BTN_EJECT
// Invoked when received Test Unit Ready command.
// return true allowing host to read/write this LUN e.g SD card inserted
bool msc_ready_callback(void)
{
  // button not active --> medium ready
  return digitalRead(BTN_EJECT) != activeState;
}
#endif
