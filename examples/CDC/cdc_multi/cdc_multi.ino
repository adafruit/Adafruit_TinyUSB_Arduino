/*
    This example demonstrates the use of multiple USB CDC/ACM "Virtual
    Serial" ports

    Written by Bill Westfield (aka WestfW), June 2021.
    Copyright 2021 by Bill Westfield
    MIT license, check LICENSE for more information
*/


/* The example creates two virtual serial ports. Text entered on
 * any of the ports will be echoed to the all ports with
 *  - all lower case in port0 (Serial)
 *  - all upper case in port1
 *
 * Requirement:
 *  The max number of CDC ports (CFG_TUD_CDC) has to be changed to at least 2.
 *  Config file is located in Adafruit_TinyUSB_Arduino/src/arduino/ports/platform/tusb_config_platform.h
 *  where platform is one of: esp32, nrf, rp2040, samd
 */

#include <Adafruit_TinyUSB.h>

#define LED LED_BUILTIN

// Create extra USB Serial Ports.  "Serial" is already created.
Adafruit_USBD_CDC USBSer1;

void setup() {
  pinMode(LED, OUTPUT);

  // start up all of the USB Vitual ports, and wait for them to enumerate.
  Serial.begin(115200);
  USBSer1.begin(115200);

  while (!Serial || !USBSer1) {
    if (Serial) {
      Serial.println("Waiting for other USB ports");
    }

    if (USBSer1) {
      USBSer1.println("Waiting for other USB ports");
    }

    delay(1000);
  }

  Serial.print("You are port 0\n\r\n0> ");
  USBSer1.print("You are port 1\n\r\n1> ");
}

int LEDstate = 0;

void loop() {
  int ch;

  ch = Serial.read();
  if (ch > 0) {
    printAll(ch);
  }

  ch = USBSer1.read();
  if (ch > 0) {
    printAll(ch);
  }

  if (delay_without_delaying(500)) {
    LEDstate = !LEDstate;
    digitalWrite(LED, LEDstate);
  }
}

// print to all CDC ports
void printAll(int ch) {
  // always lower case
  Serial.write(tolower(ch));

  // always upper case
  USBSer1.write(toupper(ch));
}

// Helper: non-blocking "delay" alternative.
boolean delay_without_delaying(unsigned long time) {
  // return false if we're still "delaying", true if time ms has passed.
  // this should look a lot like "blink without delay"
  static unsigned long previousmillis = 0;
  unsigned long currentmillis = millis();
  if (currentmillis - previousmillis >= time) {
    previousmillis = currentmillis;
    return true;
  }
  return false;
}
