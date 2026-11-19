/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2026 for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

/* This example is a USB host that sleeps between uses of a USB drive, and
 * keeps working when the drive is unplugged, swapped or power-cycled while it
 * sleeps.
 * - Device (Serial) runs on the native usb controller (roothub port0), core0
 * - Host runs on 2 GPIOs with Pico-PIO-USB (roothub port1), core1
 *
 * While asleep, the LowPower library idles core1, so the host cannot see a
 * drive being unplugged. After waking, USBHost.recheck() asks the device on the
 * host port to identify itself: one that is gone or different is unmounted and
 * the port enumerated again (tuh_msc_umount_cb() then tuh_msc_mount_cb()), an
 * unchanged one is left mounted. USBHost.recheckBusy() says when that is done.
 *
 * All drive access happens on core1, which runs the host stack: core0 only
 * sleeps, then waits until core1 has checked and listed the drive.
 *
 * Requirements:
 * - RP2040, arduino-pico core with the LowPower library, USB Stack "Adafruit TinyUSB"
 * - [Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) library
 * - 2 consecutive GPIOs: D+ is defined by PIN_USB_HOST_DP, D- = D+ +1
 * - Provide VBus (5v) and GND for peripheral
 * - CPU Speed must be either 120 or 240 Mhz. Selected via "Menu -> CPU Speed"
 */

// SdFat is required for using Adafruit_USBH_MSC_SdFatDevice
#include "SdFat_Adafruit_Fork.h"

// USBHost is defined in usbh_helper.h
#include "usbh_helper.h"

#include <LowPower.h>

// USB Host MSC Block Device object which implemented API for use with SdFat
Adafruit_USBH_MSC_BlockDevice msc_block_dev;

// file system object from SdFat
FatVolume fatfs;

// if file system is successfully mounted on usb block device
bool is_mounted = false;

// core0 asks core1 to check and list the drive, core1 says when it is done
volatile bool check_requested = false;
volatile bool check_done = false;

void setup() {
  Serial.begin(115200);
  // while ( !Serial ) delay(10);   // wait for native usb
  Serial.println("TinyUSB Host Mass Storage: sleep and recheck example");
}

void loop() {
  // Have core1 check the drive and list it. After a sleep, a changed drive takes
  // a few seconds to enumerate; one that does not answer takes longer.
  check_done = false;
  check_requested = true;
  for (uint32_t t0 = millis(); !check_done && millis() - t0 < 30000;) {
    delay(10);
  }
  if (!check_done) {
    Serial.println("The drive check did not finish");
  }

  Serial.println("Sleeping 10 s: unplug, swap or replug the drive now if you like");
  Serial.flush();
  LowPower.sleepFor(10000);
  Serial.println("Awake");
}

//------------- Core1 -------------//
static bool checking = false;

static void list_drive(void) {
  if (is_mounted) {
    fatfs.ls(&Serial, LS_SIZE);
  } else {
    Serial.println("No drive mounted");
  }
}

void setup1() {
  // configure pio-usb: defined in usbh_helper.h
  rp2040_configure_pio_usb();

  // run host stack on controller (rhport) 1
  USBHost.begin(1);
}

void loop1() {
  USBHost.task();

  // recheck() and recheckBusy() are used from the code that runs USBHost.task()
  if (check_requested) {
    check_requested = false;
    USBHost.recheck();
    checking = true;
  }
  if (checking && !USBHost.recheckBusy()) {
    checking = false;
    list_drive();
    check_done = true;
  }
}

//--------------------------------------------------------------------+
// TinyUSB Host callbacks (core1)
//--------------------------------------------------------------------+
extern "C"
{

// Invoked when a device with MassStorage interface is mounted
void tuh_msc_mount_cb(uint8_t dev_addr) {
  Serial.printf("Drive attached, address = %d\r\n", dev_addr);

  // Initialize block device with MSC device address
  msc_block_dev.begin(dev_addr);

  // For simplicity this example only support LUN 0
  msc_block_dev.setActiveLUN(0);

  is_mounted = fatfs.begin(&msc_block_dev);
}

// Invoked when a device with MassStorage interface is unmounted
void tuh_msc_umount_cb(uint8_t dev_addr) {
  Serial.printf("Drive removed, address = %d\r\n", dev_addr);

  // unmount file system
  is_mounted = false;
  fatfs.end();

  // end block device
  msc_block_dev.end();
}

}
