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

#ifndef TUSB_CONFIG_STM32_H_
#define TUSB_CONFIG_STM32_H_

// USB Port
#define CFG_TUSB_RHPORT0_MODE  OPT_MODE_DEVICE
#define CFG_TUSB_RHPORT0_SPEED OPT_FULL_SPEED
#define CFG_TUSB_RHPORT1_MODE  OPT_MODE_NONE

// MCU / OS - Auto-detect based on STM32 family
#if defined(STM32F1xx)
  #define CFG_TUSB_MCU OPT_MCU_STM32F1
#elif defined(STM32F4xx)
  #define CFG_TUSB_MCU OPT_MCU_STM32F4
#elif defined(STM32G4xx)
  #define CFG_TUSB_MCU OPT_MCU_STM32G4
#else
  #error "Unsupported STM32 family - only F1xx, F4xx and G4xx are currently supported"
#endif

#define CFG_TUSB_OS  OPT_OS_NONE

// Debug
#ifndef CFG_TUSB_DEBUG
#define CFG_TUSB_DEBUG 0
#endif

#define CFG_TUSB_MEM_SECTION
#define CFG_TUSB_MEM_ALIGN __attribute__((aligned(4)))

// Device stack
#define CFG_TUD_ENABLED        1
#define CFG_TUD_ENDPOINT0_SIZE 64

// Classes
#define CFG_TUD_CDC     1
#define CFG_TUD_MSC     1
#define CFG_TUD_HID     1
#define CFG_TUD_MIDI    1
#define CFG_TUD_VENDOR  0

// Buffer sizes
#define CFG_TUD_CDC_RX_BUFSIZE    64
#define CFG_TUD_CDC_TX_BUFSIZE    64
#define CFG_TUD_CDC_EP_BUFSIZE    64
#define CFG_TUD_HID_EP_BUFSIZE    64
#define CFG_TUD_MIDI_RX_BUFSIZE  128
#define CFG_TUD_MIDI_TX_BUFSIZE  128
#define CFG_TUD_MSC_EP_BUFSIZE   512
#define CFG_TUD_VENDOR_RX_BUFSIZE 64
#define CFG_TUD_VENDOR_TX_BUFSIZE 64

// Serial Redirect
#define Serial SerialTinyUSB

// TINYUSB_NEED_POLLING_TASK is intentionally NOT defined here.
//
// The STM32 port implements yield() to call tud_task() automatically
// whenever the Arduino core calls yield() (e.g. inside delay(), and at
// the bottom of every loop() iteration on cores that wrap loop() with
// a yield() call). This means TinyUSB is serviced without any explicit
// TinyUSBDevice.task() call in the sketch's loop().
//
// Sketches that still contain the legacy polling guard:
//
//   #ifdef TINYUSB_NEED_POLLING_TASK
//   TinyUSBDevice.task();
//   #endif
//
// will simply compile out the block, which is correct behaviour.

#endif // TUSB_CONFIG_STM32_H_