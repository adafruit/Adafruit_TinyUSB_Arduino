/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

#include "Adafruit_TinyUSB.h"

/* This sketch demonstrates USB HID mouse
 * Press button pin will move
 * - mouse toward bottom right of monitor
 * 
 * Depending on the board, the button pin
 * and its active state (when pressed) are different
 */
#if defined ARDUINO_SAMD_CIRCUITPLAYGROUND_EXPRESS
  const int pin = 4; // Left Button
  bool activeState = true;
#elif defined ARDUINO_NRF52840_FEATHER
  const int pin = 7; // UserSw
  bool activeState = false;
#else
  const int pin = 12;
  bool activeState = false;
#endif
  

// HID report descriptor using TinyUSB's template
// Single Report (no ID) descriptor
uint8_t const desc_hid_report[] =
{
  TUD_HID_REPORT_DESC_MOUSE()
};

// USB HID object
Adafruit_USBD_HID usb_hid;

// the setup function runs once when you press reset or power the board
void setup()
{
  // Set up button, pullup opposite to active state
  pinMode(pin, activeState ? INPUT_PULLDOWN : INPUT_PULLUP);

  usb_hid.setPollInterval(2);
  usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));

  usb_hid.begin();

  Serial.begin(115200);

  Serial.println("Adafruit TinyUSB HID Mouse example");
}

void loop()
{
  // poll gpio once each 10 ms
  delay(10);

  // button is active low
  uint32_t const btn = digitalRead(pin);

  // Remote wakeup
  if ( tud_suspended() && (btn == activeState) )
  {
    // Wake up host if we are in suspend mode
    // and REMOTE_WAKEUP feature is enabled by host
    USBDevice.remoteWakeup();
  }

  /*------------- Mouse -------------*/
  if ( usb_hid.ready() )
  {
    if ( btn == activeState )
    {
      int8_t const delta = 5;
      usb_hid.mouseMove(0, delta, delta); // no ID: right + down

      // delay a bit before attempt to send keyboard report
      delay(10);
    }
  }
}
