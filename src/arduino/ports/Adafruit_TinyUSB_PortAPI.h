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

#ifndef ADAFRUIT_TINYUSB_PORTAPI_H_
#define ADAFRUIT_TINYUSB_PORTAPI_H_

//--------------------------------------------------------------------+
// Porting API
//--------------------------------------------------------------------+

// To enter/reboot to bootloader
// usually when host disconnects cdc at baud 1200 (touch 1200)
void TinyUSB_Port_EnterDFU(void);

// Init device hardware.
// Called by USBDevice.begin()
void TinyUSB_Port_InitDeviceController(uint8_t rhport);

// Get unique serial number, needed for Serial String Descriptor
// Fill serial_id (raw bytes) and return its length (limit to 16 bytes)
// Note: Serial descriptor can be overwritten by user API
uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16]);

#endif
