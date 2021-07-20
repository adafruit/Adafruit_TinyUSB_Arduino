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

#if defined ARDUINO_ARCH_RP2040 && TUSB_OPT_DEVICE_ENABLED

#include "Arduino.h"

// mbed old pico-sdk need to wrap with cpp
extern "C" {
#include "hardware/flash.h"
#include "hardware/irq.h"
#include "pico/bootrom.h"
#include "pico/mutex.h"
#include "pico/time.h"
}

#include "arduino/Adafruit_TinyUSB_API.h"
#include "tusb.h"

// USB processing will be a periodic timer task
#define USB_TASK_INTERVAL 1000
#define USB_TASK_IRQ 31

//--------------------------------------------------------------------+
// Forward USB interrupt events to TinyUSB IRQ Handler
// rp2040 implementation will install appropriate handler when initializing
// tinyusb. There is no need to forward IRQ from application
//--------------------------------------------------------------------+

//--------------------------------------------------------------------+
// Earle Philhower and mbed specific
//--------------------------------------------------------------------+

// mbed use old pico-sdk does not have unique_id
#ifdef ARDUINO_ARCH_MBED

#include "mbed.h"
static mbed::Ticker _usb_ticker;

#define get_unique_id(_serial) flash_get_unique_id(_serial)

static void ticker_task(void) { irq_set_pending(USB_TASK_IRQ); }

static void setup_periodic_usb_hanlder(uint64_t us) {
  _usb_ticker.attach(ticker_task, (std::chrono::microseconds)us);
}

#else

#include "pico/unique_id.h"

#define get_unique_id(_serial)                                                 \
  pico_get_unique_board_id((pico_unique_board_id_t *)_serial);

static int64_t timer_task(__unused alarm_id_t id, __unused void *user_data) {
  irq_set_pending(USB_TASK_IRQ);
  return USB_TASK_INTERVAL;
}

static void setup_periodic_usb_hanlder(uint64_t us) {
  add_alarm_in_us(us, timer_task, NULL, true);
}

#endif // mbed

//--------------------------------------------------------------------+
// Porting API
//--------------------------------------------------------------------+

// Big, global USB mutex, shared with all USB devices to make sure we don't
// have multiple cores updating the TUSB state in parallel
mutex_t __usb_mutex;

static void usb_irq() {
  // if the mutex is already owned, then we are in user code
  // in this file which will do a tud_task itself, so we'll just do nothing
  // until the next tick; we won't starve
  if (mutex_try_enter(&__usb_mutex, NULL)) {
    tud_task();
    mutex_exit(&__usb_mutex);
  }
}

void TinyUSB_Port_InitDevice(uint8_t rhport) {
  (void)rhport;

  mutex_init(&__usb_mutex);

  irq_set_enabled(USBCTRL_IRQ, false);
  irq_handler_t current_handler = irq_get_exclusive_handler(USBCTRL_IRQ);
  if (current_handler) {
    irq_remove_handler(USBCTRL_IRQ, current_handler);
  }

  tusb_init();

  // soft irq for periodically task runner
  irq_set_exclusive_handler(USB_TASK_IRQ, usb_irq);
  irq_set_enabled(USB_TASK_IRQ, true);
  setup_periodic_usb_hanlder(USB_TASK_INTERVAL);
}

void TinyUSB_Port_EnterDFU(void) {
  reset_usb_boot(0, 0);
  while (1) {
  }
}

uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16]) {
  get_unique_id(serial_id);
  return FLASH_UNIQUE_ID_SIZE_BYTES;
}

//--------------------------------------------------------------------+
// Core API
// Implement Core API since rp2040 need mutex for calling tud_task in
// IRQ context
//--------------------------------------------------------------------+

extern "C" {

void TinyUSB_Device_Task(void) {
  // Since tud_task() is also invoked in ISR, we need to get the mutex first
  if (mutex_try_enter(&__usb_mutex, NULL)) {
    tud_task();
    mutex_exit(&__usb_mutex);
  }
}
}

#endif
