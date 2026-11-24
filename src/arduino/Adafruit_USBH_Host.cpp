/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2022, Ha Thach (tinyusb.org) for Adafruit
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

#ifdef ARDUINO_ARCH_ESP32
#include "arduino/ports/esp32/tusb_config_esp32.h"
#include "driver/gpio.h"
#include <Arduino.h>
#endif

#include "tusb_option.h"

#if CFG_TUH_ENABLED

#include "Adafruit_TinyUSB_API.h"
#include "Adafruit_USBH_Host.h"

Adafruit_USBH_Host *Adafruit_USBH_Host::_instance = NULL;

Adafruit_USBH_Host::Adafruit_USBH_Host(void) {
  Adafruit_USBH_Host::_instance = this;
  _rhport = 0;
#if defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421
  _spi = NULL;
  _cs = _intr = _sck = _mosi = _miso = -1;
#endif
}

#if defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421

// API to read MAX3421's register. Implemented by TinyUSB
extern "C" uint8_t tuh_max3421_reg_read(uint8_t rhport, uint8_t reg,
                                        bool in_isr);

// API to write MAX3421's register. Implemented by TinyUSB
extern "C" bool tuh_max3421_reg_write(uint8_t rhport, uint8_t reg, uint8_t data,
                                      bool in_isr);

static void max3421_isr(void);

#if defined(ARDUINO_ARCH_ESP32)
SemaphoreHandle_t max3421_intr_sem;
// Held by the interrupt task while it runs the handler. end() takes it to wait
// for a handler that is running, and clears max3421_intr_enabled under it.
static SemaphoreHandle_t max3421_intr_lock;
static volatile bool max3421_intr_enabled;
static void max3421_intr_task(void *param);
#endif

Adafruit_USBH_Host::Adafruit_USBH_Host(SPIClass *spi, int8_t cs, int8_t intr) {
  Adafruit_USBH_Host::_instance = this;
  _rhport = 0;
  _spi = spi;
  _cs = cs;
  _intr = intr;
  _sck = _mosi = _miso = -1;
}

Adafruit_USBH_Host::Adafruit_USBH_Host(SPIClass *spi, int8_t sck, int8_t mosi,
                                       int8_t miso, int8_t cs, int8_t intr) {
  Adafruit_USBH_Host::_instance = this;
  _rhport = 0;
  _spi = spi;
  _cs = cs;
  _intr = intr;
  _sck = sck;
  _mosi = mosi;
  _miso = miso;
}

uint8_t Adafruit_USBH_Host::max3421_readRegister(uint8_t reg, bool in_isr) {
  return tuh_max3421_reg_read(_rhport, reg, in_isr);
}

bool Adafruit_USBH_Host::max3421_writeRegister(uint8_t reg, uint8_t data,
                                               bool in_isr) {
  return tuh_max3421_reg_write(_rhport, reg, data, in_isr);
}

#endif

bool Adafruit_USBH_Host::configure(uint8_t rhport, uint32_t cfg_id,
                                   const void *cfg_param) {
  return tuh_configure(rhport, cfg_id, cfg_param);
}

#ifdef ARDUINO_ARCH_RP2040
bool Adafruit_USBH_Host::configure_pio_usb(uint8_t rhport,
                                           const void *cfg_param) {
  return configure(rhport, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, cfg_param);
}
#endif

bool Adafruit_USBH_Host::begin(uint8_t rhport) {
#if defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421
  if (_intr < 0 || _cs < 0 || !_spi) {
    return false;
  }

  // CS pin
  pinMode(_cs, OUTPUT);
  digitalWrite(_cs, HIGH);

  // SPI init
  // Software SPI is not supported yet
#ifdef ARDUINO_ARCH_ESP32
  // ESP32 SPI assign pins when begin() of declaration as standard API
  _spi->begin(_sck, _miso, _mosi, -1);

  // Create an task for executing interrupt handler in thread mode.
  // Only once: begin() is called again after end().
  if (max3421_intr_sem == NULL) {
    max3421_intr_sem = xSemaphoreCreateBinary();
    max3421_intr_lock = xSemaphoreCreateMutex();
    if (max3421_intr_sem == NULL || max3421_intr_lock == NULL ||
        xTaskCreateUniversal(max3421_intr_task, "max3421 intr", 2048, NULL, 5,
                             NULL, ARDUINO_RUNNING_CORE) != pdPASS) {
      // undo, so that a later begin() tries again
      if (max3421_intr_sem != NULL) {
        vSemaphoreDelete(max3421_intr_sem);
        max3421_intr_sem = NULL;
      }
      if (max3421_intr_lock != NULL) {
        vSemaphoreDelete(max3421_intr_lock);
        max3421_intr_lock = NULL;
      }
      return false;
    }
  }
#else
  _spi->begin();
#endif

  // Interrupt pin. On ESP32 the interrupt task runs the handler only once
  // tusb_init() below has set up the driver (a notification from before an
  // end() may still be pending).
  pinMode(_intr, INPUT_PULLUP);
  attachInterrupt(_intr, max3421_isr, FALLING);
#endif

  _rhport = rhport;
  const tusb_rhport_init_t rh_init = {
      .role = TUSB_ROLE_HOST,
      .speed = TUH_OPT_HIGH_SPEED ? TUSB_SPEED_HIGH : TUSB_SPEED_FULL,
  };
  bool const ok = tusb_init(rhport, &rh_init);

#if defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421 && defined(ARDUINO_ARCH_ESP32)
  if (ok) {
    xSemaphoreTake(max3421_intr_lock, portMAX_DELAY);
    max3421_intr_enabled = true;
    xSemaphoreGive(max3421_intr_lock);
    // run the handler once: an interrupt during tusb_init() was skipped
    xSemaphoreGive(max3421_intr_sem);
  }
#endif

  return ok;
}

void Adafruit_USBH_Host::task(uint32_t timeout_ms, bool in_isr) {
  tuh_task_ext(timeout_ms, in_isr);
}

bool Adafruit_USBH_Host::end(void) {
#if defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421
  // No interrupt handler may run once tuh_deinit() frees the driver's state
#ifdef ARDUINO_ARCH_ESP32
  if (max3421_intr_lock != NULL) {
    // Wait for a handler that is running. It can wait for space in the host
    // event queue, which only task() empties: keep emptying it meanwhile
    // (end() runs where task() runs). The interrupt stays on until the lock
    // is held, so work that task() runs here (e.g. a mount callback that
    // reads the drive) can still complete.
    while (xSemaphoreTake(max3421_intr_lock, pdMS_TO_TICKS(1)) != pdTRUE) {
      tuh_task_ext(0, false);
    }
    // a notification that is still pending is then ignored
    max3421_intr_enabled = false;
    detachInterrupt(_intr);
    xSemaphoreGive(max3421_intr_lock);
  } else {
    detachInterrupt(_intr);
  }
#else
  detachInterrupt(_intr);
#endif
#endif

  bool const ok = tuh_deinit(_rhport);

#if defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421
  if (!ok) {
    // the host is still running: give it its interrupt back
#ifdef ARDUINO_ARCH_ESP32
    max3421_intr_enabled = true;
#endif
    attachInterrupt(_intr, max3421_isr, FALLING);
  }
#endif

  return ok;
}

// Invoked when received report from device via interrupt endpoint
TU_ATTR_WEAK void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance,
                                             uint8_t const *report,
                                             uint16_t len) {
  (void)dev_addr;
  (void)instance;
  (void)report;
  (void)len;
}

//--------------------------------------------------------------------+
// USB Host using MAX3421E
//--------------------------------------------------------------------+
#if CFG_TUH_ENABLED && defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421

#if defined(ARDUINO_ARCH_ESP32)

// ESP32 out-of-sync
#if ESP_ARDUINO_VERSION < ESP_ARDUINO_VERSION_VAL(2, 0, 14) &&                 \
    !defined(PLATFORMIO)
extern "C" void hcd_int_handler_esp32(uint8_t rhport, bool in_isr);
#define tuh_int_handler_esp32 hcd_int_handler_esp32

#else
#define tuh_int_handler_esp32 tuh_int_handler

#endif

static void max3421_intr_task(void *param) {
  (void)param;

  while (1) {
    xSemaphoreTake(max3421_intr_sem, portMAX_DELAY);
    xSemaphoreTake(max3421_intr_lock, portMAX_DELAY);
    if (max3421_intr_enabled) {
      tuh_int_handler_esp32(1, false);
    }
    xSemaphoreGive(max3421_intr_lock);
  }
}

static void max3421_isr(void) {
  BaseType_t xHigherPriorityTaskWoken = pdFALSE;
  xSemaphoreGiveFromISR(max3421_intr_sem, &xHigherPriorityTaskWoken);
  if (xHigherPriorityTaskWoken) {
    portYIELD_FROM_ISR();
  }
}

#else

static void max3421_isr(void) { tuh_int_handler(1, true); }

#endif // ESP32

extern "C" {

void tuh_max3421_spi_cs_api(uint8_t rhport, bool active) {
  (void)rhport;

  if (!Adafruit_USBH_Host::_instance) {
    return;
  }
  Adafruit_USBH_Host *host = Adafruit_USBH_Host::_instance;
  SPIClass *spi = host->_spi;

  if (active) {
    // MAX3421e max clock is 26MHz
    // Depending on mcu ports, it may need to be clipped down
#ifdef ARDUINO_ARCH_SAMD
    // SAMD 21/51 can only work reliably at 12MHz
    uint32_t const max_clock = 12000000ul;
#else
    uint32_t const max_clock = 26000000ul;
#endif

    spi->beginTransaction(SPISettings(max_clock, MSBFIRST, SPI_MODE0));
    digitalWrite(Adafruit_USBH_Host::_instance->_cs, LOW);
  } else {
    spi->endTransaction();
    digitalWrite(Adafruit_USBH_Host::_instance->_cs, HIGH);
  }
}

bool tuh_max3421_spi_xfer_api(uint8_t rhport, uint8_t const *tx_buf,
                              uint8_t *rx_buf, size_t xfer_bytes) {
  (void)rhport;

  if (!Adafruit_USBH_Host::_instance) {
    return false;
  }
  Adafruit_USBH_Host *host = Adafruit_USBH_Host::_instance;
  SPIClass *spi = host->_spi;

#ifdef ARDUINO_ARCH_SAMD
  // SAMD cannot use transfer(tx_buf, rx_buf, len) API since it default to use
  // DMA. However, since this can be invoked within EIC_Handler (ISR) which may
  // have priority higher than DMA ISR. That will cause blocking wait for
  // dma_busy not working
  for (size_t count = 0; count < xfer_bytes; count++) {
    uint8_t data = 0x00;
    if (tx_buf) {
      data = tx_buf[count];
    }
    data = spi->transfer(data);

    if (rx_buf) {
      rx_buf[count] = data;
    }
  }
#elif defined(ARDUINO_ARCH_ESP32)
  spi->transferBytes(tx_buf, rx_buf, xfer_bytes);
#else
  spi->transfer(tx_buf, rx_buf, xfer_bytes);
#endif

  return true;
}

void tuh_max3421_int_api(uint8_t rhport, bool enabled) {
  (void)rhport;

  if (!Adafruit_USBH_Host::_instance) {
    return;
  }
  Adafruit_USBH_Host *host = Adafruit_USBH_Host::_instance;
  (void)host;

#ifdef ARDUINO_ARCH_SAMD
  //--- SAMD51 ---//
#ifdef __SAMD51__
  const IRQn_Type irq =
      (IRQn_Type)(EIC_0_IRQn + g_APinDescription[host->_intr].ulExtInt);

  if (enabled) {
    NVIC_EnableIRQ(irq);
  } else {
    NVIC_DisableIRQ(irq);
  }
#else
  //--- SAMD21 ---//
  if (enabled) {
    NVIC_EnableIRQ(EIC_IRQn);
  } else {
    NVIC_DisableIRQ(EIC_IRQn);
  }
#endif

#elif defined(ARDUINO_NRF52_ADAFRUIT)
  //--- nRF52 ---//
  if (enabled) {
    NVIC_EnableIRQ(GPIOTE_IRQn);
  } else {
    NVIC_DisableIRQ(GPIOTE_IRQn);
  }

#elif defined(ARDUINO_ARCH_ESP32)
  //--- ESP32 ---//
  if (enabled) {
    gpio_intr_enable((gpio_num_t)host->_intr);
  } else {
    gpio_intr_disable((gpio_num_t)host->_intr);
  }

#elif defined(ARDUINO_ARCH_RP2040)
  //--- RP2040 ---//
  irq_set_enabled(IO_IRQ_BANK0, enabled);

#else
#error "MAX3421e host is not supported by this architecture"
#endif
}

} // extern C
#endif

#endif
