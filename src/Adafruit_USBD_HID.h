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

#ifndef ADAFRUIT_USBD_HID_H_
#define ADAFRUIT_USBD_HID_H_

#include "Adafruit_TinyUSB_core.h"

class Adafruit_USBD_HID : Adafruit_USBD_Interface
{
  public:
    Adafruit_USBD_HID(void);

    void setBootProtocol(uint8_t protocol); // 0: None, 1: Keyboard, 2:Mouse
    void setReportDescriptor(uint8_t const* desc_report, uint16_t len);

    bool begin(void);

    // from Adafruit_USBD_Interface
    virtual uint16_t getDescriptor(uint8_t* buf, uint16_t bufsize);

  private:
    uint8_t _protocol;
    uint8_t const* _desc_report;
    uint16_t _desc_report_len;
};

#endif /* ADAFRUIT_USBD_HID_H_ */
