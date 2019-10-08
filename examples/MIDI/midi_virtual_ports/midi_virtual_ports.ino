/* This sketch is enumerated as USB MIDI device which shows up at the
   host as a single MIDI interface with 3 virtual ports/wires.

    Every port continuously sends out notes:
      port 1, channel 1: monochord (single tone)
      port 2, channel 4: dichord (two tones)
      port 3, channel 8: trichord (three tones)

    Notes received on the ports let the built-in LED blink one, two,
    or three times.

    MIDI.h is a small wrapper around the USB MIDI packet interface
    provided by Adafruit_USBD_MIDI device.
*/

#include "MIDI.h"

// MIDI interface with 3 virtual ports/wires
static MIDI MIDI(3);

void setup() {
  Serial.begin(9600);
  MIDI.begin();
}

static unsigned long loop_millis;
static uint8_t loop_position;

void loop() {
  // Received MIDI packet
  static MIDIPacket in;

  // Receive packet and let the LED blink as many times as
  // the port number
  if (MIDI.receive(&in)) {
    Serial.print("Received: Port=");
    Serial.print(in.getPort() + 1);
    Serial.print(" Channel=");
    Serial.print(in.getChannel() + 1);
    Serial.print(" Type=");
    Serial.print(in.getType());
    Serial.print(" Note=");
    Serial.print(in.getNote());
    Serial.print(" Value=");
    Serial.println(in.getNoteVelocity());
    if (in.getType() == MIDIPacket::NoteOn) {
      for (uint8_t i = 0; i < in.getPort() + 1; i++) {
        digitalWrite(LED_BUILTIN, HIGH);
        delay(75);
        digitalWrite(LED_BUILTIN, LOW);
        delay(75);
      }
    }
  }

  // Packets to send out at a defined port
  static MIDIPacket out1(0);
  static MIDIPacket out2(1);
  static MIDIPacket out3(2);

  // Send as many notes as the port number, once every 4 seconds
  if ((unsigned long)(millis() - loop_millis) > 1000) {
    switch (loop_position) {
      case 0:
        // One note (C) on all ports
        MIDI.send(out1.setNote(0, 60, 64));
        MIDI.send(out2.setNote(3, 60, 64));
        MIDI.send(out3.setNote(7, 60, 64));
        break;

      case 1:
        // Second note (E) on port 2 and 3
        MIDI.send(out2.setNote(3, 64, 64));
        MIDI.send(out3.setNote(7, 64, 64));
        break;

      case 2:
        // Third note (G) on port3
        MIDI.send(out3.setNote(7, 67, 64));
        break;

      case 3:
        // All notes off
        MIDI.send(out1.setNote(0, 60, 0));
        MIDI.send(out2.setNote(3, 60, 0));
        MIDI.send(out3.setNote(7, 60, 0));
        MIDI.send(out2.setNote(3, 64, 0));
        MIDI.send(out3.setNote(7, 64, 0));
        MIDI.send(out3.setNote(7, 67, 0));
    }

    loop_position++;
    if (loop_position == 4)
      loop_position = 0;

    loop_millis = millis();
  }
}
