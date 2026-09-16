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

#if (defined(ARDUINO_ARCH_STM32) ||                                            \
     defined(ARDUINO_ARCH_ARDUINO_CORE_STM32)) &&                              \
    CFG_TUD_ENABLED

#ifndef USE_HAL_DRIVER
#define USE_HAL_DRIVER
#endif
#include "Arduino.h"
#include "arduino/Adafruit_TinyUSB_API.h"

#if defined(STM32H7xx)
#include "stm32h7xx_hal.h"
#include "stm32h7xx_hal_rcc.h"
#elif defined(STM32WBAxx)
#include "stm32wbaxx_hal.h"
#include "stm32wbaxx_hal_rcc.h"
#elif defined(STM32F7xx)
#include "stm32f7xx_hal.h"
#include "stm32f7xx_hal_rcc.h"
#elif defined(STM32F4xx)
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_rcc.h"
#elif defined(STM32WBxx)
#include "stm32wbxx_hal.h"
#include "stm32wbxx_hal_rcc.h"
#else
// #warning "This TinyUSB port only supports the STM32F4, STM32F7, STM32H7,
// STM32WB and STM32WBA families"
#endif

#include "tusb.h"

// STM32WB (classic, e.g. WB55) is the odd one out here: it has no OTG_FS/HS
// peripheral at all, only ST's older "FSDEV" full-speed device controller
// (the same IP block used by F0/F1/F3/G0/G4/L0/L1/L4/L5). That means a
// completely different bring-up (no GPIO AF needed on DM/DP, two separate
// interrupt vectors instead of one, its own clock-enable/voltage-domain
// calls) - handled in its own branch below rather than trying to fold it
// into the OTG_FS-style macros used by every other supported family.
#if !defined(STM32WBxx) || defined(STM32WBAxx)

// Most STM32 device-mode USB is a single "OTG_FS" peripheral on PA11/PA12
// (AF10). Some STM32H7 parts (e.g. H723/H730/H733/H735/H7A3/H7B0/H7B3) don't
// have a separate OTG_FS instance: they only have OTG_HS, wired here to the
// same PA11/PA12 pins via its internal full-speed-only transceiver. The AF
// name, clock-enable macro and even the interrupt vector name differ in that
// case (confirmed against the CMSIS startup files: those parts only have an
// "OTG_HS_IRQHandler" vector, no "OTG_FS_IRQHandler" at all). STM32WBA
// (the newer, bigger WBA6x chips - smaller WBA5x parts have no USB at all)
// is the same situation again, but with yet another IRQ/AF naming scheme
// ("USB_OTG_HS_IRQn"/"GPIO_AF10_USB_OTG_HS" instead of "OTG_HS_IRQn"/
// "GPIO_AF10_OTG1_FS").
#if defined(STM32H7xx) && !defined(USB2_OTG_FS)
#define TUSB_STM32_FS_IRQn OTG_HS_IRQn
#define TUSB_STM32_FS_IRQHandler OTG_HS_IRQHandler
#define TUSB_STM32_FS_AF GPIO_AF10_OTG1_FS
#define TUSB_STM32_FS_CLK_ENABLE() __HAL_RCC_USB_OTG_HS_CLK_ENABLE()
#elif defined(STM32H7xx)
#define TUSB_STM32_FS_IRQn OTG_FS_IRQn
#define TUSB_STM32_FS_IRQHandler OTG_FS_IRQHandler
#define TUSB_STM32_FS_AF GPIO_AF10_OTG2_FS
#define TUSB_STM32_FS_CLK_ENABLE() __HAL_RCC_USB2_OTG_FS_CLK_ENABLE()
#elif defined(STM32WBAxx)
#define TUSB_STM32_FS_IRQn USB_OTG_HS_IRQn
#define TUSB_STM32_FS_IRQHandler USB_OTG_HS_IRQHandler
#define TUSB_STM32_FS_AF GPIO_AF10_USB_OTG_HS
#define TUSB_STM32_FS_CLK_ENABLE() __HAL_RCC_USB_OTG_HS_CLK_ENABLE()
#else
#define TUSB_STM32_FS_IRQn OTG_FS_IRQn
#define TUSB_STM32_FS_IRQHandler OTG_FS_IRQHandler
#define TUSB_STM32_FS_AF GPIO_AF10_OTG_FS
#define TUSB_STM32_FS_CLK_ENABLE() __HAL_RCC_USB_OTG_FS_CLK_ENABLE()
#endif

#endif // !STM32WBxx || STM32WBAxx

//--------------------------------------------------------------------+
// Forward USB interrupt events to TinyUSB IRQ Handler
//--------------------------------------------------------------------+
extern "C" {

#if defined(STM32WBxx) && !defined(STM32WBAxx)
// WB55's FSDEV controller splits device events across two NVIC vectors
// instead of OTG_FS/HS's single one.
void USB_HP_IRQHandler(void) { tud_int_handler(0); }
void USB_LP_IRQHandler(void) { tud_int_handler(0); }
#else
void TUSB_STM32_FS_IRQHandler(void) { tud_int_handler(0); }
#endif

void yield(void) {
  tud_task();
  if (tud_cdc_connected()) {
    tud_cdc_write_flush();
  }
}

} // extern "C"

// stm32duino's main() calls loop() then serialEventRun() on every single
// iteration, unconditionally - unlike yield(), which only runs if the sketch
// happens to call delay() or another yield()-ing function. Overriding this
// weak symbol guarantees tud_task() gets pumped every loop() iteration even
// if the sketch never calls TinyUSBDevice.task() itself (without this, a
// loop() with no delay()/task() call leaves USB SETUP packets queued but
// never serviced, so the host times out during enumeration).
//
// This replaces the core's default serialEventRun(), which dispatches the
// legacy serialEventN() callbacks for real hardware UARTs (Serial1, Serial2,
// etc; the default "Serial" is TinyUSB's own CDC here, unaffected). That
// dispatch logic is reproduced below so boards with multiple hardware UARTs
// keep working as before.
void serialEventRun(void) {
  tud_task();
  if (tud_cdc_connected()) {
    tud_cdc_write_flush();
  }

#if defined(HAVE_HWSERIAL1)
  if (serialEvent1 && Serial1.available()) {
    serialEvent1();
  }
#endif
#if defined(HAVE_HWSERIAL2)
  if (serialEvent2 && Serial2.available()) {
    serialEvent2();
  }
#endif
#if defined(HAVE_HWSERIAL3)
  if (serialEvent3 && Serial3.available()) {
    serialEvent3();
  }
#endif
#if defined(HAVE_HWSERIAL4)
  if (serialEvent4 && Serial4.available()) {
    serialEvent4();
  }
#endif
#if defined(HAVE_HWSERIAL5)
  if (serialEvent5 && Serial5.available()) {
    serialEvent5();
  }
#endif
#if defined(HAVE_HWSERIAL6)
  if (serialEvent6 && Serial6.available()) {
    serialEvent6();
  }
#endif
#if defined(HAVE_HWSERIAL7)
  if (serialEvent7 && Serial7.available()) {
    serialEvent7();
  }
#endif
#if defined(HAVE_HWSERIAL8)
  if (serialEvent8 && Serial8.available()) {
    serialEvent8();
  }
#endif
#if defined(HAVE_HWSERIAL9)
  if (serialEvent9 && Serial9.available()) {
    serialEvent9();
  }
#endif
#if defined(HAVE_HWSERIAL10)
  if (serialEvent10 && Serial10.available()) {
    serialEvent10();
  }
#endif
#if defined(HAVE_HWSERIALLP1)
  if (serialEventLP1 && SerialLP1.available()) {
    serialEventLP1();
  }
#endif
#if defined(HAVE_HWSERIALLP2)
  if (serialEventLP2 && SerialLP2.available()) {
    serialEventLP2();
  }
#endif
#if defined(HAVE_HWSERIALLP3)
  if (serialEventLP3 && SerialLP3.available()) {
    serialEventLP3();
  }
#endif
}

//--------------------------------------------------------------------+
// Porting API
//--------------------------------------------------------------------+
void TinyUSB_Port_InitDevice(uint8_t rhport) {
  (void)rhport;

#if defined(STM32WBxx) && !defined(STM32WBAxx)
  // WB55: FSDEV controller. DM/DP (PA11/PA12) are dedicated pins that the
  // peripheral takes over automatically once enabled - no GPIO AF setup is
  // required (ST's own reference bring-up configures them as plain
  // GPIO_MODE_INPUT and calls that "optional, for guidance only"). Like H7,
  // the USB PHY here is on its own voltage domain (VDDUSB) that must be
  // powered up explicitly.
  HAL_PWREx_EnableVddUSB();
  __HAL_RCC_USB_CLK_ENABLE();

  NVIC_SetPriority(USB_HP_IRQn, 0);
  NVIC_EnableIRQ(USB_HP_IRQn);
  NVIC_SetPriority(USB_LP_IRQn, 0);
  NVIC_EnableIRQ(USB_LP_IRQn);
#else
  // Enable clocks FIRST
  __HAL_RCC_GPIOA_CLK_ENABLE();
  TUSB_STM32_FS_CLK_ENABLE();

#if defined(STM32H7xx)
  // H7's USB PHY is powered from a separate VDD33USB supply; the internal
  // regulator/monitor must be enabled or the peripheral won't work reliably.
  HAL_PWREx_EnableUSBVoltageDetector();
#endif

  // Configure USB pins (PA11 = DM, PA12 = DP)
  GPIO_InitTypeDef GPIO_InitStruct = {};
  GPIO_InitStruct.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStruct.Alternate = TUSB_STM32_FS_AF;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  // Enable USB IRQ
  NVIC_SetPriority(TUSB_STM32_FS_IRQn, 0);
  NVIC_EnableIRQ(TUSB_STM32_FS_IRQn);

  // Note: VBUS sensing (bus-powered vs self-powered) is configured by
  // TinyUSB's own dwc2 driver (see dwc2_stm32_gccfg_cfg()) as part of
  // tusb_init() below, since the GCCFG bit layout differs across STM32
  // families (e.g. F4 vs F7).
#endif

  // Initialize TinyUSB device stack
  const tusb_rhport_init_t rh_init = {
      .role = TUSB_ROLE_DEVICE,
      .speed = TUD_OPT_HIGH_SPEED ? TUSB_SPEED_HIGH : TUSB_SPEED_FULL,
  };
  tusb_init(rhport, &rh_init);
}

void TinyUSB_Port_EnterDFU(void) {
  // Optional - implement bootloader entry if needed
}

uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16]) {
  // UID_BASE is the 96-bit unique device ID base address, provided by the
  // chip-specific CMSIS header. It varies even within a family (e.g.
  // STM32F722/F723/F730/F732/F733 use a different address than other F7
  // parts), so use the macro rather than a hardcoded literal.
  volatile uint32_t *uid = (volatile uint32_t *)UID_BASE;
  uint32_t *serial_32 = (uint32_t *)serial_id;
  serial_32[0] = uid[0];
  serial_32[1] = uid[1];
  serial_32[2] = uid[2];
  return 12;
}

#endif // ARDUINO_ARCH_STM32
