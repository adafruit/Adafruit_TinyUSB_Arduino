/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2019, hathach for Adafruit
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

#include "tusb_option.h"

#if defined ARDUINO_ARCH_RP2040 & TUSB_OPT_DEVICE_ENABLED

#include "Arduino.h"
#include "arduino/Adafruit_USBD_Device.h"

#include "pico/bootrom.h"
#include "pico/unique_id.h"

//--------------------------------------------------------------------+
// Forward USB interrupt events to TinyUSB IRQ Handler
// rp2040 implementation will install approriate handler when initializing
// tinyusb. There is no need to forward IRQ from application
//--------------------------------------------------------------------+

//--------------------------------------------------------------------+
// Porting API
//--------------------------------------------------------------------+

void TinyUSB_Port_InitDevice(uint8_t rhport)
{
  (void) rhport;

  // no specific hardware initialization
  // TOOD maybe set up sdtio usb
}

void TinyUSB_Port_EnterDFU(void)
{
  reset_usb_boot(0,0);
  while(1) {}
}

uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16])
{
  pico_get_unique_board_id( (pico_unique_board_id_t*) serial_id );
  return PICO_UNIQUE_BOARD_ID_SIZE_BYTES;
}

#endif // USE_TINYUSB
