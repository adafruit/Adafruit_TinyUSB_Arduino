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

/* This sketch demonstrates WebUSB
 */

// USB WebUSB object
Adafruit_USBD_WebUSB usb_web;

// Landing Page: scheme (0: http, 1: https), url
const tusb_desc_webusb_url_t landingPage = TUD_WEBUSB_URL_DESCRIPTOR(1 /*https*/, "adafruit.github.io/Adafruit_TinyUSB_Arduino/examples/webusb-serial");

int led_pin = LED_BUILTIN;

// the setup function runs once when you press reset or power the board
void setup()
{
  pinMode(led_pin, OUTPUT);
  digitalWrite(led_pin, LOW);
  
  usb_web.begin();
  usb_web.setLandingPage(&landingPage);
  usb_web.setLineStateCallback(line_state_callback);

  Serial.begin(115200);

  // wait until device mounted
  while( !USBDevice.mounted() ) delay(1);

  Serial.println("Adafruit TinyUSB WebUSB example");
}

void loop()
{
//  delay(1000);
//  digitalWrite(LED_BUILTIN, 1-digitalRead(LED_BUILTIN));
}

void line_state_callback(bool connected)
{
  digitalWrite(led_pin, connected);
}
