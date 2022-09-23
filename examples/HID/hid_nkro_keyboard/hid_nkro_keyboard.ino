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

/* This sketch demonstrates USB HID NKRO keyboard.
 * This sketch is only valid on boards which have native USB support
 * and compatibility with Adafruit TinyUSB library.
 * For example SAMD21, RP2040, ATMEGA32U4.
 *
 * Make sure you select the TinyUSB USB stack.
 * You can test the keyboard in a notepad application.
 */

// HID report descriptor using TinyUSB's template
// Single Report (no ID) descriptor
uint8_t const desc_hid_report[] =
    {
        TUD_HID_REPORT_DESC_NKROKEYBOARD()
    };

// USB HID object. For ESP32 these values cannot be changed after this declaration
// desc report, desc len, protocol, interval, use out endpoint
Adafruit_USBD_HID usb_hid(desc_hid_report, sizeof(desc_hid_report), HID_ITF_PROTOCOL_NONE, 2, true);

// Report payload defined in src/class/hid/hid.h
// - 1byte for modifiers
// - 13bytes for keys
// - 1byte for custom key
hid_nkrokeyboard_report_t kb;

void setup()
{
#if defined(ARDUINO_ARCH_MBED) && defined(ARDUINO_ARCH_RP2040)
  // Manual begin() is required on core without built-in support for TinyUSB such as mbed rp2040
  TinyUSB_Device_Init(0);
#endif

  Serial.begin(115200);

  // Notes: following commented-out functions has no affect on ESP32
  // usb_hid.setPollInterval(2);
  // usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));

  usb_hid.begin();

  // wait until device mounted
  while (!TinyUSBDevice.mounted())
    delay(1);

  Serial.println("Adafruit TinyUSB HID NKRO keyboard example");
}

void add(uint8_t key_value)
{
  kb.keys[key_value / 8] |= 1 << (key_value % 8);
}

void releaseall()
{
  kb.modifier = 0;
  kb.custom = 0;
  for (int i = 0; i < 13; i++)
  {
    kb.keys = 0;
  }
}

void send()
{
  usb_hid.sendReport(0, &kb, sizeof(kb));
}

void loop()
{
  if (!usb_hid.ready())
    return;

  // Reset buttons
  Serial.println("No pressing buttons");
  releaseall();
  send();
  delay(2000);

  // Press A
  Serial.println("Press A");
  add(HID_KEY_A); // HID_KEY_A, HID_KEY_B...etc defined in src/class/hid/hid.h
  send();
  delay(2000);
  releaseall();
  send();

  // Press B
  Serial.println("Press B");
  add(HID_KEY_B);
  send();
  delay(2000);
  releaseall();
  send();

  // Press Enter
  Serial.println("Press Enter");
  add(HID_KEY_ENTER);
  send();
  delay(2000);
  releaseall();
  send();

  // Press caps lock
  Serial.println("Press caps lock");
  add(HID_KEY_CAPS_LOCK);
  send();
  delay(2000);
  releaseall();
  send();
}