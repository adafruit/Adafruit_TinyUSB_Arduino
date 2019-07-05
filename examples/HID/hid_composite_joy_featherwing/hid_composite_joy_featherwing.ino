/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

/* This sketch demonstrates USB HID mouse using Joystick Feather Wing
 *  https://www.adafruit.com/product/3632
 *
 * Following library is required
 *  - Adafruit_seesaw
 */

#include "Adafruit_TinyUSB.h"
#include "Adafruit_seesaw.h"

#define BUTTON_RIGHT 6
#define BUTTON_DOWN  7
#define BUTTON_LEFT  9
#define BUTTON_UP    10
#define BUTTON_SEL   14
uint32_t button_mask = (1 << BUTTON_RIGHT) | (1 << BUTTON_DOWN) |
                (1 << BUTTON_LEFT) | (1 << BUTTON_UP) | (1 << BUTTON_SEL);

Adafruit_seesaw ss;

// HID report descriptor using TinyUSB's template
// Single Report (no ID) descriptor
uint8_t const desc_hid_report[] =
{
  TUD_HID_REPORT_DESC_MOUSE()
};

// USB HID object
Adafruit_USBD_HID usb_hid;

int last_x, last_y;

// the setup function runs once when you press reset or power the board
void setup()
{
  usb_hid.setPollInterval(2);
  usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));

  usb_hid.begin();

  Serial.begin(115200);
  Serial.println("Adafruit TinyUSB HID Mouse with Joy FeatherWing example");

  if(!ss.begin(0x49)){
    Serial.println("ERROR! seesaw not found");
    while(1);
  } else {
    Serial.println("seesaw started");
    Serial.print("version: ");
    Serial.println(ss.getVersion(), HEX);
  }
  ss.pinModeBulk(button_mask, INPUT_PULLUP);
  ss.setGPIOInterrupts(button_mask, 1);

  last_y = ss.analogRead(2);
  last_x = ss.analogRead(3);
}

void loop()
{
  // poll gpio once each 10 ms
  delay(10);

  int y = ss.analogRead(2);
  int x = ss.analogRead(3);

  int dx = x - last_x;
  int dy = y - last_y;

  if ( (abs(dx) > 3) || (abs(dy) > 30) )
  {
    // Remote wakeup if PC is suspended
    if ( USBDevice.suspended() )
    {
      // Wake up host if we are in suspend mode
      // and REMOTE_WAKEUP feature is enabled by host
      USBDevice.remoteWakeup();
    }

    /*------------- Mouse -------------*/
    if ( usb_hid.ready() )
    {
      usb_hid.mouseMove(0, dx, dy); // no ID: right + down

      last_x = x;
      last_y = y;

      // delay a bit before attempt to send keyboard report
      delay(10);
    }
  }
}
