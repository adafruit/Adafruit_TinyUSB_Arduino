/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2018, hathach for Adafruit
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

#ifndef TUSB_CONFIG_ARDUINO_H_
#define TUSB_CONFIG_ARDUINO_H_

#ifdef __cplusplus
 extern "C" {
#endif

#if defined(ARDUINO_ARCH_SAMD)
  #include "arduino/ports/samd/tusb_config_samd.h"

#elif defined(ARDUINO_NRF52_ADAFRUIT)
  #include "arduino/ports/nrf/tusb_config_nrf.h"

#elif defined(ARDUINO_ARCH_RP2040)
  #include "arduino/ports/rp2040/tusb_config_rp2040.h"

#elif defined(ARDUINO_ARCH_ESP32)
  // Note: when compiling core Arduino IDEs will include tusb_config.h in the BSP
  // sdk/include/arduino_tinyusb/include. While compiling .c file in this library this
  // file will be used instead. For consistency: include the one in BSP here as well
  #include "sdkconfig.h"
  #if CONFIG_IDF_TARGET_ESP32S2 || CONFIG_IDF_TARGET_ESP32S3
    #include "../../arduino_tinyusb/include/tusb_config.h"
  #else
    #include "arduino/ports/esp32/tusb_config_esp32.h"
  #endif

  // Note: For platformio prioritize this file over the one in BSP in all cases
#elif defined(ARDUINO_ARCH_CH32) || defined(CH32V20x) || defined(CH32V30x) || defined(CH32X035) || defined(CH32L10x)
  #include "arduino/ports/ch32/tusb_config_ch32.h"
#else
  #error TinyUSB Arduino Library does not support your core yet
#endif

// Debug TinyUSB with Serial1
#if CFG_TUSB_DEBUG
#define CFG_TUSB_DEBUG_PRINTF log_printf
#endif

#ifdef __cplusplus
 }
#endif

#endif
