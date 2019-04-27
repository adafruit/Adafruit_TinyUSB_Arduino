/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2019 Ha Thach for Adafruit Industries
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#include "Adafruit_TinyUSB.h"

/* This sketch demonstrates multiple report USB HID.
 * Press button pin will move
 * - mouse toward bottom right of monitor
 * - send 'a' key
 */

// Report ID
enum
{
  RID_KEYBOARD = 1,
  RID_MOUSE
};

// HID report descriptor using TinyUSB's template
uint8_t const desc_hid_report[] =
{
  HID_REPORT_DESC_KEYBOARD( HID_REPORT_ID(RID_KEYBOARD), ),
  HID_REPORT_DESC_MOUSE   ( HID_REPORT_ID(RID_MOUSE), )
};

Adafruit_USBD_HID usb_hid;

const int pin = 7;

// the setup function runs once when you press reset or power the board
void setup()
{
  usb_hid.setPollInterval(2);
  usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));

  usb_hid.begin();

  // Set up button
  pinMode(pin, INPUT_PULLUP);

  Serial.begin(115200);
  while ( !Serial ) delay(10);   // wait for native usb

  Serial.println("Adafruit TinyUSB Mass Storage Disk RAM example");
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
      usb_hid.mouseMove(RID_MOUSE, delta, delta); // right + down

      // delay a bit before attempt to send keyboard report
      delay(2);
    }
  }

  /*------------- Keyboard -------------*/
  if ( usb_hid.ready() )
  {
    // use to avoid send multiple consecutive zero report for keyboard
    static bool has_key = false;

    if ( btn )
    {
      uint8_t keycode[6] = { 0 };
      keycode[0] = HID_KEY_A;

      usb_hid.keyboadReport(RID_KEYBOARD, 0, keycode);

      has_key = true;
    }else
    {
      // send empty key report if previously has key pressed
      if (has_key) usb_hid.keyboardKeyRelease(RID_KEYBOARD);
      has_key = false;
    }
  }
}
