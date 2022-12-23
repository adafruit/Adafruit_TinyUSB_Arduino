/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/


/* This example demonstrates use of Host Serial (CDC). SerialHost (declared below) is
 * an object to manage an CDC peripheral connected to our USB Host connector. This example
 * will forward all characters from Serial to SerialHost and vice versa.
 *
 * Note:
 * - Device run on native usb controller (controller0)
 * - Host run on bit-banging 2 GPIOs with the help of Pico-PIO-USB library (controller1)

 * Requirements:
 * - [Pico-PIO-USB](https://github.com/sekigon-gonnoc/Pico-PIO-USB) library
 * - 2 consecutive GPIOs: D+ is defined by HOST_PIN_DP (gpio20), D- = D+ +1 (gpio21)
 * - Provide VBus (5v) and GND for peripheral
 * - CPU Speed must be either 120 or 240 Mhz. Selected via "Menu -> CPU Speed"
 */

// pio-usb is required for rp2040 host
#include "pio_usb.h"

// TinyUSB lib
#include "Adafruit_TinyUSB.h"

// Pin D+ for host, D- = D+ + 1
#define HOST_PIN_DP       20

// Pin for enabling Host VBUS. comment out if not used
#define HOST_PIN_VBUS_EN        22
#define HOST_PIN_VBUS_EN_STATE  1

// USB Host object
Adafruit_USBH_Host USBHost;

// CDC Host object
Adafruit_USBH_CDC  SerialHost;

//--------------------------------------------------------------------+
// Setup and Loop on Core0
//--------------------------------------------------------------------+

void setup() {
  Serial1.begin(115200);

  Serial.begin(115200);
  while ( !Serial ) delay(10);   // wait for native usb

  Serial.println("TinyUSB Host Serial Echo Example");
}

void loop()
{
  uint8_t buf[64];

  // Serial -> SerialHost
  if (Serial.available()) {
    size_t count = Serial.read(buf, sizeof(buf));
    if ( SerialHost && SerialHost.connected() ) {
      SerialHost.write(buf, count);
      SerialHost.flush();
    }
  }

  // SerialHost -> Serial
  if ( SerialHost.connected() && SerialHost.available() ) {
    size_t count = SerialHost.read(buf, sizeof(buf));
    Serial.write(buf, count);
  }
}

//--------------------------------------------------------------------+
// Setup and Loop on Core1
//--------------------------------------------------------------------+

void setup1() {
  while ( !Serial ) delay(10);   // wait for native usb
  Serial.println("Core1 setup to run TinyUSB host with pio-usb");

  // Check for CPU frequency, must be multiple of 120Mhz for bit-banging USB
  uint32_t cpu_hz = clock_get_hz(clk_sys);
  if ( cpu_hz != 120000000UL && cpu_hz != 240000000UL ) {
    while ( !Serial ) {
      delay(10);   // wait for native usb
    }
    Serial.printf("Error: CPU Clock = %u, PIO USB require CPU clock must be multiple of 120 Mhz\r\n", cpu_hz);
    Serial.printf("Change your CPU Clock to either 120 or 240 Mhz in Menu->CPU Speed \r\n", cpu_hz);
    while(1) {
      delay(1);
    }
  }

#ifdef HOST_PIN_VBUS_EN
  pinMode(HOST_PIN_VBUS_EN, OUTPUT);

  // power off first
  digitalWrite(HOST_PIN_VBUS_EN, 1-HOST_PIN_VBUS_EN_STATE);
  delay(1);

  // power on
  digitalWrite(HOST_PIN_VBUS_EN, HOST_PIN_VBUS_EN_STATE);
  delay(10);
#endif

  pio_usb_configuration_t pio_cfg = PIO_USB_DEFAULT_CONFIG;
  pio_cfg.pin_dp = HOST_PIN_DP;
  USBHost.configure_pio_usb(1, &pio_cfg);

  // run host stack on controller (rhport) 1
  // Note: For rp2040 pico-pio-usb, calling USBHost.begin() on core1 will have most of the
  // host bit-banging processing works done in core1 to free up core0 for other works
  USBHost.begin(1);
}

void loop1()
{
  USBHost.task();

  // periodically flush SerialHost if connected
  if ( SerialHost && SerialHost.connected() ) {
    SerialHost.flush();
  }
}

//--------------------------------------------------------------------+
// TinyUSB Host callbacks
//--------------------------------------------------------------------+

// Invoked when a device with CDC interface is mounted
// idx is index of cdc interface in the internal pool.
void tuh_cdc_mount_cb(uint8_t idx) {
  // bind SerialHost object to this interface index
  SerialHost.begin(idx);

  Serial.println("SerialHost is connected to a new CDC device");
}

// Invoked when a device with CDC interface is unmounted
void tuh_cdc_umount_cb(uint8_t idx) {
  if (idx == SerialHost.getIndex()) {
    // unbind SerialHost if this interface is unmounted
    SerialHost.end();

    Serial.println("SerialHost is disconnected");
  }
}
