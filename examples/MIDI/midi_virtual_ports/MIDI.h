#pragma once

#include <Adafruit_TinyUSB.h>

// The USB MIDI packet
// Every packet is 4 bytes long, only the status byte has the 7th bit set:
//   1. header (4 bits virtual port/wire number + 4th bit is set + 3 bits type)
//   2. status (7th bit is set, 3 bits type + 4 bits channel/system number)
//   3. data byte 1 (7 bit)
//   4. data byte 2 (7 bit)
class MIDIPacket {
  public:
    // 3 bits type
    enum PacketType {
      NoteOff =           0,
      NoteOn =            1,
      Aftertouch =        2,
      ControlChange =     3,
      ProgramChange =     4,
      AftertouchChannel = 5,
      PitchBend =         6,
      System =            7
    };

    MIDIPacket();

    // Set virtual port/wire in the packet. Port 1 == 0
    MIDIPacket(uint8_t port);
    uint8_t getPort();
    void setPort(uint8_t port);

    // Channel 1 == 0
    uint8_t getChannel();

    PacketType getType();

    uint8_t getNote();
    uint8_t getNoteVelocity();

    // Encode values into the packet and return a pointer to it. The result
    // can be passed to the send() method.
    // Example which sends out a Middle-C note at channel 1 with velocity 80:
    //   #include <MIDI.h>
    //   static MIDI MIDI;
    //   static MIDIPacket midi;
    //   MIDI.begin();
    //   MIDI.send(midi.setNote(0, 60, 80));
    const MIDIPacket* set(uint8_t channel, PacketType type, uint8_t data1, uint8_t data2);
    const MIDIPacket* setNote(uint8_t channel, uint8_t note, uint8_t velocity);

  private:
    friend class MIDI;
    uint8_t _port;
    uint8_t _data[4];
};

// The USB MIDI device
class MIDI {
  public:
    MIDI();
    MIDI(uint8_t n_ports);
    void begin();
    bool send(const MIDIPacket* packet);
    bool receive(MIDIPacket* packet);

  private:
    Adafruit_USBD_MIDI _device;
};
