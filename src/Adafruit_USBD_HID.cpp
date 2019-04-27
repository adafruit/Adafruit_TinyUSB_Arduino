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

#include "Adafruit_USBD_HID.h"


//--------------------------------------------------------------------+
//
//--------------------------------------------------------------------+
#define EPOUT   0x00
#define EPIN    0x80

uint8_t const _ascii2keycode[128][2] =  { HID_ASCII_TO_KEYCODE };

//------------- IMPLEMENTATION -------------//
Adafruit_USBD_HID::Adafruit_USBD_HID(void)
{
  _interval_ms = 10;
  _protocol = HID_PROTOCOL_NONE;
  _desc_report = NULL;
  _desc_report_len = 0;
}

void Adafruit_USBD_HID::setPollInterval(uint8_t interval_ms)
{
  _interval_ms = interval_ms;
}

void Adafruit_USBD_HID::setBootProtocol(uint8_t protocol)
{
  _protocol = protocol;
}

void Adafruit_USBD_HID::setReportDescriptor(uint8_t const* desc_report, uint16_t len)
{
  _desc_report = desc_report;
  _desc_report_len = len;
}

void Adafruit_USBD_HID::setReportCallback(get_report_callback_t get_report, set_report_callback_t set_report)
{
  _get_report_cb = get_report;
  _set_report_cb = set_report;
}

uint16_t Adafruit_USBD_HID::getDescriptor(uint8_t* buf, uint16_t bufsize)
{
  if ( !_desc_report_len ) return 0;

  uint8_t desc[] = { TUD_HID_DESCRIPTOR(0, 0, _protocol, _desc_report_len, EPIN, 16, _interval_ms) };
  uint16_t const len = sizeof(desc);

  if ( bufsize < len ) return 0;
  memcpy(buf, desc, len);
  return len;
}

bool Adafruit_USBD_HID::begin(void)
{
  if ( !USBDevice.addInterface(*this) ) return false;
  tud_desc_set.hid_report = _desc_report;

  return true;
}

bool Adafruit_USBD_HID::ready(void)
{
  return tud_hid_ready();
}

bool Adafruit_USBD_HID::sendReport(uint8_t report_id, void const* report, uint8_t len)
{
  return tud_hid_report(report_id, report, len);
}

//--------------------------------------------------------------------+
// Keyboard
//--------------------------------------------------------------------+

bool Adafruit_USBD_HID::keyboadReport(uint8_t report_id, uint8_t modifier, uint8_t keycode[6])
{
  return tud_hid_keyboard_report(report_id, modifier, keycode);
}

bool Adafruit_USBD_HID::keyboardPress(uint8_t report_id, char ch)
{
  uint8_t keycode[6] = { 0 };
  uint8_t modifier   = 0;

  if ( _ascii2keycode[ch][0] ) modifier = KEYBOARD_MODIFIER_LEFTSHIFT;
  keycode[0] = _ascii2keycode[ch][1];
  tud_hid_keyboard_report(report_id, modifier, keycode);
}

bool Adafruit_USBD_HID::keyboardRelease(uint8_t report_id)
{
  return tud_hid_keyboard_key_release(report_id);
}

//--------------------------------------------------------------------+
// Mouse
//--------------------------------------------------------------------+

bool Adafruit_USBD_HID::mouseReport(uint8_t report_id, uint8_t buttons, int8_t x, int8_t y, int8_t scroll, int8_t pan)
{
  return tud_hid_mouse_report(report_id, buttons, x, y, scroll, pan);
}

bool Adafruit_USBD_HID::mouseMove(uint8_t report_id, int8_t x, int8_t y)
{
  return tud_hid_mouse_move(report_id, x, y);
}

bool Adafruit_USBD_HID::mouseScroll(uint8_t report_id, int8_t scroll, int8_t pan)
{
  return tud_hid_mouse_scroll(report_id, scroll, pan);
}

bool Adafruit_USBD_HID::mouseButtonPress(uint8_t report_id, uint8_t buttons)
{
  return tud_hid_mouse_report(report_id, buttons, 0, 0, 0, 0);
}

bool Adafruit_USBD_HID::mouseButtonRelease(uint8_t report_id)
{
  return tud_hid_mouse_report(report_id, 0, 0, 0, 0, 0);
}

