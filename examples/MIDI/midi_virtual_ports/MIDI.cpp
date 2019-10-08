#include "MIDI.h"

MIDIPacket::MIDIPacket() {}

MIDIPacket::MIDIPacket(uint8_t port) :
  _port(port) {}

uint8_t MIDIPacket::getPort() {
  return (_data[0] >> 4);
}

void MIDIPacket::setPort(uint8_t port) {
  _port = port;
}

uint8_t MIDIPacket::getChannel() {
  return (_data[1] & 0x0f);
}

MIDIPacket::PacketType MIDIPacket::getType() {
    return static_cast<PacketType>((_data[1] >> 4) & 0x07);
}

uint8_t MIDIPacket::getNote() {
  return _data[2];
}

uint8_t MIDIPacket::getNoteVelocity() {
  return _data[3];
}

const MIDIPacket* MIDIPacket::set(uint8_t channel, PacketType type, uint8_t data1, uint8_t data2) {
  _data[0] = (_port << 4) | (0x08 | type);
  _data[1] = 0x80 | (type << 4) | channel;
  _data[2] = data1;
  _data[3] = data2;
  return this;
}

const MIDIPacket* MIDIPacket::setNote(uint8_t channel, uint8_t note, uint8_t velocity) {
  if (velocity == 0)
    return set(channel, NoteOff, note, 64);

  return set(channel, NoteOn, note, velocity);
}

MIDI::MIDI() :
  _device() {}

MIDI::MIDI(uint8_t n_ports) :
  _device(n_ports) {}

void MIDI::begin() {
  _device.begin();
}

bool MIDI::send(const MIDIPacket* packet) {
  return _device.send(packet->_data);
}

bool MIDI::receive(MIDIPacket* packet) {
  return _device.receive(packet->_data);
}
