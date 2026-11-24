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
 * drive being unplugged. So the host stack is stopped before the sleep
 * (USBHost.end(): the drive is unmounted) and started again after it
 * (USBHost.begin(): the drive that is attached now is enumerated and mounted).
 *
 * All drive access happens on core1, which runs the host stack: core0 only
 * sleeps, and asks core1 to list the drive, to stop the host and to start it.
 *
 * Requirements:
 * - RP2040, arduino-pico core with the LowPower library, USB Stack "Adafruit TinyUSB"
 * - [Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) library
 * - 2 consecutive GPIOs: D+ is defined by PIN_USB_HOST_DP, D- = D+ +1
 * - Provide VBus (5v) and GND for peripheral
 * - CPU Speed must be either 120 or 240 Mhz. Selected via "Menu -> CPU Speed"
 * - USBHost.end() needs hcd_deinit() for Pico-PIO-USB in the TinyUSB of this
 *   library (hathach/tinyusb#4586): until this library has it, the example
 *   prints that the host cannot be stopped, and does not sleep.
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
volatile bool is_mounted = false;

// core0 asks core1 to do one of these, then waits until core1 sets it to NONE
enum { REQ_NONE, REQ_LIST, REQ_STOP, REQ_START };
volatile int core1_request = REQ_NONE;

// the result of the last USBHost.end(): false if this host cannot be stopped
volatile bool host_stopped = false;

static void ask_core1(int request) {
  core1_request = request;
  while (core1_request != REQ_NONE) {
    delay(10);
  }
}

void setup() {
  Serial.begin(115200);
  // while ( !Serial ) delay(10);   // wait for native usb
  Serial.println("TinyUSB Host Mass Storage: sleep example");
}

void loop() {
  // A drive takes about a second to enumerate and mount after begin()
  for (uint32_t t0 = millis(); !is_mounted && millis() - t0 < 10000;) {
    delay(10);
  }
  ask_core1(REQ_LIST);

  // Stop the host stack: the drive is unmounted
  ask_core1(REQ_STOP);
  if (!host_stopped) {
    // e.g. a TinyUSB without hcd_deinit() for this host controller
    Serial.println("USBHost.end() failed: this host cannot be stopped, so this example cannot sleep");
    while (1) {
      delay(1000);
    }
  }

  Serial.println("Sleeping 10 s: unplug, swap or replug the drive now if you like");
  Serial.flush();
  LowPower.sleepFor(10000);
  Serial.println("Awake");

  // Start the host stack again: the drive that is attached now is mounted
  ask_core1(REQ_START);
}

//------------- Core1 -------------//
static bool host_running = false;

void setup1() {
  // configure pio-usb: defined in usbh_helper.h
  rp2040_configure_pio_usb();

  // run host stack on controller (rhport) 1
  host_running = USBHost.begin(1);
}

void loop1() {
  if (host_running) {
    USBHost.task();
  }

  switch (core1_request) {
    case REQ_LIST:
      if (is_mounted) {
        fatfs.ls(&Serial, LS_SIZE);
      } else {
        Serial.println("No drive mounted");
      }
      break;

    case REQ_STOP:
      // end() unmounts the drive (tuh_msc_umount_cb() below)
      host_stopped = USBHost.end();
      host_running = false;
      break;

    case REQ_START:
      host_running = USBHost.begin(1);
      break;

    default:
      return;
  }
  core1_request = REQ_NONE;
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
