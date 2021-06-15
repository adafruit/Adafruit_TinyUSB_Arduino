/*
    This example demonstrates the use of multiple USB CDC/ACM "Virtual
    Serial" ports, using Ha Thach's tinyUSB library, and the port of
    that library to the Arduino environment

    https://github.com/hathach/tinyusb
    https://github.com/adafruit/Adafruit_TinyUSB_Arduino

    Written by Bill Westfield (aka WestfW), June 2021.
    Copyright 2021 by Bill Westfield
    MIT license, check LICENSE for more information

    The example creates three virtual serial ports.  Text entered on
    any of the ports will be echoed to the other two ports.

    The max number of CDC ports (CFG_TUD_CDC) has to be changed to at
    least 3, changed in the core tusb_config.h file.
*/

#include <Adafruit_TinyUSB.h>

#define LED LED_BUILTIN

/*
      Create extra USB Serial Ports.  "Serial" is already created.
*/
Adafruit_USBD_CDC USBSer2(1);
Adafruit_USBD_CDC USBSer3(2);

Adafruit_USBD_CDC *ports[CFG_TUD_CDC] = { &Serial, &USBSer2, &USBSer3, NULL };

void setup() {
  pinMode(LED, OUTPUT);
  // start up all of the USB Vitual ports, and wait for them to enumerate.
  Serial.begin(115200);   // Do these in order, or
  USBSer2.begin(115200);  //   "bad things will happen"
  USBSer3.begin(115200);
  while (!Serial || !USBSer2 || !USBSer3) {
    if (Serial) {
      Serial.println("Waiting for other USB ports");
    }
    if (USBSer2) {
      USBSer2.println("Waiting for other USB ports");
    }
    if (USBSer3) {
      USBSer3.println("Waiting for other USB ports");
    }
    delay(1000);
  }
  Serial.print("You are port 0\n\r\n0> ");
  USBSer2.print("You are port 1\n\r\n1> ");
  USBSer3.print("You are port 2\n\r\n2> ");
}

int LEDstate = 0;

void loop() {
  int ch;
  ch = Serial.read();
  if (ch > 0) {
    USBSer2.write(ch);
    USBSer3.write(ch);
  }
  ch = USBSer2.read();
  if (ch > 0) {
    Serial.write(ch);
    USBSer3.write(ch);
  }
  ch = USBSer3.read();
  if (ch > 0) {
    Serial.write(ch);
    USBSer2.write(ch);
  }
  if (delay_without_delaying(500)) {
    LEDstate = !LEDstate;
    digitalWrite(LED, LEDstate);
  }
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

// Helper: non-blocking line-at-a-time read.
