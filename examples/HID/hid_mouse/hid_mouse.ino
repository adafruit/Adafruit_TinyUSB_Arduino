// The MIT License (MIT)
// Copyright (c) 2019 Ha Thach for Adafruit Industries

#include "Adafruit_TinyUSB.h"

/* This sketch demonstrates USB HID mouse
 * Press button pin will move
 * - mouse toward bottom right of monitor
 */

// HID report descriptor using TinyUSB's template
// Single Report (no ID) descriptor
uint8_t const desc_hid_report[] =
{
  TUD_HID_REPORT_DESC_MOUSE()
};

Adafruit_USBD_HID usb_hid;

const int pin = 7;

// the setup function runs once when you press reset or power the board
void setup()
{
  // Set up button
  pinMode(pin, INPUT_PULLUP);

  usb_hid.setPollInterval(2);
  usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));

  usb_hid.begin();

  Serial.begin(115200);
  while ( !Serial ) delay(10);   // wait for native usb

  Serial.println("Adafruit TinyUSB HID Mouse example");
  Serial.print("Wire pin "); Serial.print(pin); Serial.println(" to GND to move cursor to bottom right corner.")
}

void loop()
{
  // poll gpio once each 10 ms
  delay(10);

  // button is active low
  uint32_t const btn = 1 - digitalRead(pin);

  // Remote wakeup
  if ( tud_suspended() && btn )
  {
    // Wake up host if we are in suspend mode
    // and REMOTE_WAKEUP feature is enabled by host
    tud_remote_wakeup();
  }

  /*------------- Mouse -------------*/
  if ( usb_hid.ready() )
  {
    if ( btn )
    {
      int8_t const delta = 5;
      usb_hid.mouseMove(0, delta, delta); // no ID: right + down

      // delay a bit before attempt to send keyboard report
      delay(10);
    }
  }
}
