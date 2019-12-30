/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2019 hathach for Adafruit Industries
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

#ifndef ADAFRUIT_USBD_MIDI_H_
#define ADAFRUIT_USBD_MIDI_H_

#include "Adafruit_TinyUSB_Core.h"

class Adafruit_USBD_MIDI : public Stream, Adafruit_USBD_Interface {
public:
  Adafruit_USBD_MIDI(void);
  Adafruit_USBD_MIDI(uint8_t n_cables);

  bool begin(void);

  // for MIDI library
  bool begin(uint32_t baud) {
    (void)baud;
    return begin();
  }

  // Stream interface to use with MIDI Library
  virtual int read(void);
  virtual size_t write(uint8_t b);
  virtual int available(void);
  virtual int peek(void);
  virtual void flush(void);

  // from Adafruit_USBD_Interface
  virtual uint16_t getDescriptor(uint8_t itfnum, uint8_t *buf,
                                 uint16_t bufsize);

private:
  uint8_t _n_cables;
};

#endif /* ADAFRUIT_USBD_MIDI_H_ */
