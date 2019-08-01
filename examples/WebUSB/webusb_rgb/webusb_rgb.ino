/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

/* This sketch demonstrates WebUSB as web serial with Chrome browser.
 * After enumerated successfully, Chrome will pop-up notification
 * with URL to landing page, click on it to test
 *  - Click "Connect" and select device, When connected the on-board LED will litted up.
 *  - Any charaters received from either webusb/Serial will be echo back to webusb and Serial
 *  
 * Note: 
 * - The WebUSB landing page notification is currently disabled in Chrome 
 * on Windows due to Chromium issue 656702 (https://crbug.com/656702). You have to 
 * go to https://adafruit.github.io/Adafruit_TinyUSB_Arduino/examples/webusb-serial to test
 * 
 * - On Windows 7 and prior: You need to use Zadig tool to manually bind the 
 * WebUSB interface with the WinUSB driver for Chrome to access. From windows 8 and 10, this
 * is done automatically by firmware.
 */

#include "Adafruit_TinyUSB.h"
#include <Adafruit_NeoPixel.h>

// Which pin on the Arduino is connected to the NeoPixels?
// On a Trinket or Gemma we suggest changing this to 1
// use on-board neopixel PIN_NEOPIXEL if existed
#ifdef PIN_NEOPIXEL
  #define PIN      PIN_NEOPIXEL
#else
  #define PIN      6
#endif

// How many NeoPixels are attached to the Arduino?
#define NUMPIXELS  1

// When we setup the NeoPixel library, we tell it how many pixels, and which pin to use to send signals.
// Note that for older NeoPixel strips you might need to change the third parameter--see the strandtest
// example for more information on possible values.
Adafruit_NeoPixel pixels = Adafruit_NeoPixel(NUMPIXELS, PIN, NEO_GRB + NEO_KHZ800);

// USB WebUSB object
Adafruit_USBD_WebUSB usb_web;

// Landing Page: scheme (0: http, 1: https), url
WEBUSB_URL_DEF(landingPage, 1 /*https*/, "adafruit.github.io/Adafruit_TinyUSB_Arduino/examples/webusb-rgb");

// the setup function runs once when you press reset or power the board
void setup()
{
  usb_web.begin();
  usb_web.setLandingPage(&landingPage);
  usb_web.setLineStateCallback(line_state_callback);

  Serial.begin(115200);

  // This initializes the NeoPixel with RED
  pixels.begin();
  pixels.setBrightness(50);
  pixels.setPixelColor(0, 0xff0000);
  pixels.show();

  // wait until device mounted
  while( !USBDevice.mounted() ) delay(1);

  Serial.println("TinyUSB WebUSB RGB example");
}

void loop()
{
  // Landing Page send 3 bytes of color as RGB
  if (usb_web.available() < 3 ) return;

  pixels.setPixelColor(0, usb_web.read(), usb_web.read(), usb_web.read());
  pixels.show();
}

void line_state_callback(bool connected)
{
  // connected = green, disconnected = red
  pixels.setPixelColor(0, connected ? 0x00ff00 : 0xff0000);
  pixels.show();
}
