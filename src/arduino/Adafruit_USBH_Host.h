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

#ifndef ADAFRUIT_USBH_HOST_H_
#define ADAFRUIT_USBH_HOST_H_

#include "Adafruit_USBD_Interface.h"
#include "tusb.h"
#include <SPI.h>

#ifdef ARDUINO_ARCH_ESP32
#include "esp32-hal-tinyusb.h"
#endif

extern "C" {
void tuh_max3421_spi_cs_api(uint8_t rhport, bool active);
bool tuh_max3421_spi_xfer_api(uint8_t rhport, uint8_t const *tx_buf,
                              size_t tx_len, uint8_t *rx_buf, size_t rx_len);
void tuh_max3421_int_api(uint8_t rhport, bool enabled);
}

class Adafruit_USBH_Host {
private:
  SPIClass *_spi;
  int8_t _cs;
  int8_t _intr;

public:
  // default constructor
  Adafruit_USBH_Host(void);

  // constructor for using MAX3421E (host shield)
  Adafruit_USBH_Host(SPIClass *spi, int8_t cs, int8_t intr);

  bool configure(uint8_t rhport, uint32_t cfg_id, const void *cfg_param);

#ifdef ARDUINO_ARCH_RP2040
  bool configure_pio_usb(uint8_t rhport, const void *cfg_param);
#endif

  bool begin(uint8_t rhport);
  void task(void);

  //------------- internal usage -------------//
  static Adafruit_USBH_Host *_instance;

private:
  //  uint16_t const *descrip`tor_string_cb(uint8_t index, uint16_t langid);
  //
  //  friend uint8_t const *tud_descriptor_device_cb(void);
  //  friend uint8_t const *tud_descriptor_configuration_cb(uint8_t index);
  //  friend uint16_t const *tud_descriptor_string_cb(uint8_t index,
  //                                                  uint16_t langid);

  friend void tuh_max3421_spi_cs_api(uint8_t rhport, bool active);
  friend bool tuh_max3421_spi_xfer_api(uint8_t rhport, uint8_t const *tx_buf,
                                       size_t tx_len, uint8_t *rx_buf,
                                       size_t rx_len);
  friend void tuh_max3421_int_api(uint8_t rhport, bool enabled);
};

#endif
