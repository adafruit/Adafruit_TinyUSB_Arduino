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

/* This sketch demonstrates USB HID keyboard.
 * - PIN A0-A5 is used to send digit '0' to '5' respectively
 *   (On the RP2040, pins D0-D5 used)
 * - LED will be used as Caplock indicator
 */

// HID report descriptor using TinyUSB's template
// Single Report (no ID) descriptor
uint8_t const desc_hid_report[] =
{
  TUD_HID_REPORT_DESC_KEYBOARD()
};

Adafruit_USBD_HID usb_hid;

// Array of pins and its keycode
// For keycode definition see BLEHidGeneric.h
#ifdef ARDUINO_ARCH_RP2040
  uint8_t pins[]    = { D0, D1, D2, D3, D4, D5 };
#else
  uint8_t pins[]    = { A0, A1, A2, A3, A4, A5 };
#endif

uint8_t hidcode[] = { HID_KEY_ARROW_RIGHT, HID_KEY_ARROW_LEFT, HID_KEY_ARROW_DOWN, HID_KEY_ARROW_UP , HID_KEY_4, HID_KEY_5 };

uint8_t pincount = sizeof(pins)/sizeof(pins[0]);

// the setup function runs once when you press reset or power the board
void setup()
{
#if defined(ARDUINO_ARCH_MBED) && defined(ARDUINO_ARCH_RP2040)
  // Manual begin() is required on core without built-in support for TinyUSB such as mbed rp2040
  TinyUSB_Device_Init(0);
#endif

  usb_hid.setBootProtocol(HID_ITF_PROTOCOL_KEYBOARD);
  usb_hid.setPollInterval(2);
  usb_hid.setReportDescriptor(desc_hid_report, sizeof(desc_hid_report));
  usb_hid.setReportCallback(NULL, hid_report_callback);
  //usb_hid.setStringDescriptor("TinyUSB Keyboard");

  usb_hid.begin();

  // led pin
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);

  // Set up pin as input
  for (uint8_t i=0; i<pincount; i++)
  {
    pinMode(pins[i], INPUT_PULLUP);
  }

  // wait until device mounted
  while( !TinyUSBDevice.mounted() ) delay(1);
}


void loop()
{
  // poll gpio once each 2 ms
  delay(2);

  // used to avoid send multiple consecutive zero report for keyboard
  static bool keyPressedPreviously = false;

  uint8_t count=0;
  uint8_t keycode[6] = { 0 };

  // scan normal key and send report
  for(uint8_t i=0; i < pincount; i++)
  {
    if ( 0 == digitalRead(pins[i]) )
    {
      // if pin is active (low), add its hid code to key report
      keycode[count++] = hidcode[i];

      // 6 is max keycode per report
      if (count == 6) break;
    }
  }

  if ( TinyUSBDevice.suspended() && count )
  {
    // Wake up host if we are in suspend mode
    // and REMOTE_WAKEUP feature is enabled by host
    TinyUSBDevice.remoteWakeup();
  }

  // skip if hid is not ready e.g still transferring previous report
  if ( !usb_hid.ready() ) return;

  if ( count )
  {
    // Send report if there is key pressed
    uint8_t const report_id = 0;
    uint8_t const modifier = 0;

    keyPressedPreviously = true;
    usb_hid.keyboardReport(report_id, modifier, keycode);
  }else
  {
    // Send All-zero report to indicate there is no keys pressed
    // Most of the time, it is, though we don't need to send zero report
    // every loop(), only a key is pressed in previous loop()
    if ( keyPressedPreviously )
    {
      keyPressedPreviously = false;
      usb_hid.keyboardRelease(0);
    }
  }
}

// Output report callback for LED indicator such as Caplocks
void hid_report_callback(uint8_t report_id, hid_report_type_t report_type, uint8_t const* buffer, uint16_t bufsize)
{
  (void) report_id;
  (void) bufsize;
  // LED indicator is output report with only 1 byte length
  if ( report_type != HID_REPORT_TYPE_OUTPUT ) return;

  // The LED bit map is as follows: (also defined by KEYBOARD_LED_* )
  // Kana (4) | Compose (3) | ScrollLock (2) | CapsLock (1) | Numlock (0)
  uint8_t ledIndicator = buffer[0];

  // turn on LED if caplock is set
  digitalWrite(LED_BUILTIN, ledIndicator & KEYBOARD_LED_CAPSLOCK);
}
