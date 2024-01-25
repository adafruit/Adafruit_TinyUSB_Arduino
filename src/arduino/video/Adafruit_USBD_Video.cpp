/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2024 Ha Thach (tinyusb.org)
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
 *
 * This file is part of the TinyUSB stack.
 */

#include "tusb_option.h"

#if CFG_TUD_ENABLED && CFG_TUD_VIDEO && CFG_TUD_VIDEO_STREAMING

#include "Adafruit_USBD_Video.h"

Adafruit_USBD_Video::Adafruit_USBD_Video(uint8_t const *desc_itf,
                                         size_t desc_len) {
  _desc_itf = desc_itf;
  _desc_len = desc_len;
  _vc_id = 0;
}

bool Adafruit_USBD_Video::begin() {
  if (!TinyUSBDevice.addInterface(*this)) {
    return false;
  }

  return true;
}

uint16_t Adafruit_USBD_Video::getInterfaceDescriptor(uint8_t itfnum,
                                                     uint8_t *buf,
                                                     uint16_t bufsize) {
  (void)itfnum;

  if (!buf || bufsize < _desc_len) {
    return false;
  }

  memcpy(buf, _desc_itf, _desc_len);
  return _desc_len;
}

//--------------------------------------------------------------------+
// API
//--------------------------------------------------------------------+

// bool Adafruit_USBD_Video::isStreaming(uint8_t stream_idx) {
//   return tud_video_n_streaming(_vc_id, stream_idx);
// }

#endif
