/*
    This example demonstrates the use of multiple USB CDC/ACM "Virtual Serial" ports,
    using Ha Thach's tinyUSB library, and the port of that library to the Arduino environment
    https://github.com/hathach/tinyusb
    https://github.com/adafruit/Adafruit_TinyUSB_Arduino

    Written by Bill Westfield (aka WestfW), June 2021.
    This code is released to the public domain (note that this is a different
    license than the rest of the TinyUSB library and examples.)

    The example creates three virtual serial ports, runs a non-blocking parser on
    each one, and allows them to send messages to each other.  This is pretty useless,
    and is relatively complex for an example, but it exercises a bunch of the CDC features.

    The max number of CDC ports (CFG_TUD_CDC) has to be changed to at least 3, changed in
    the core tusb_config.h file.
*/

#include <Adafruit_TinyUSB.h>
#include "simpleparser.h"

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
  Serial.print("You are TTY0\n\r\n0> ");
  USBSer2.print("You are TTY1\n\r\n1> ");
  USBSer3.print("You are TTY2\n\r\n2> ");
}


// We need a parser for each Virtual Serial port
simpleParser<80> line0(Serial);
simpleParser<80> line1(USBSer2);
simpleParser<80> line2(USBSer3);

int LEDstate = 0;

// Given an input port and an output port and a message, send it off.

void sendmsg(Adafruit_USBD_CDC &out, Adafruit_USBD_CDC &in, char *msg) {
  out.print("\r\nTTY");
  out.print(in.getInstance());
  out.print(": ");
  out.println(msg);
  out.flush();
}

// we've received a line on some port.  Check if it's a valid comand,
// parse the arguments, and take appropriate action
void parseMsg(Adafruit_USBD_CDC &in, parserCore &parser) {
  int target;
  enum {                    CMD_SEND, CMD_HELP, CMD_HELP2 };
  int cmd = parser.keyword("send help ? ");
  switch (cmd) {
    case CMD_SEND:
      target = parser.number();
      if (target < 0 || target >= CFG_TUD_CDC || ports[target] == NULL) {
        in.println("Bad target line");
        return;
      }
      sendmsg(*ports[target], in, parser.restOfLine());
      break;

    case CMD_HELP:
    case CMD_HELP2:
      in.println("Available commands:\r\n  SEND N msg");
      break;
    default:
      in.println("invalid command");
      break;
  }
}

void loop() {
  if (line0.getLine()) {
    parseMsg(Serial, line0);
    line0.reset();
    Serial.print("0> ");

  }
  if (line1.getLine()) {
    parseMsg(USBSer2, line1);
    line1.reset();
    USBSer2.print("1> ");
  }
  if (line2.getLine()) {
    parseMsg(USBSer3, line2);
    line2.reset();
    USBSer3.print("2> ");
  }
  if (delay_without_delaying(500)) {
    // blink LED to show we're still alive
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
