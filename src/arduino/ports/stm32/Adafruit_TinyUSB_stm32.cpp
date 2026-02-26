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

#define USE_HAL_DRIVER

// Include appropriate HAL headers based on MCU family
#if defined(STM32F1xx)
#include "stm32f1xx_hal.h"
#include "stm32f1xx_hal_rcc.h"
#elif defined(STM32F4xx)
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_rcc.h"
#elif defined(STM32G4xx)
#include "stm32g4xx_hal.h"
#include "stm32g4xx_hal_rcc.h"
#include "stm32g4xx_hal_rcc_ex.h"
#define GPIO_AF10_USB                                                          \
  0x0AU // AF10 = USB FS on STM32G4xx (no named macro in HAL)
#elif defined(STM32WBxx)
#include "stm32wbxx_hal.h"
#include "stm32wbxx_hal_gpio_ex.h"
#include "stm32wbxx_hal_rcc.h"
#include "stm32wbxx_hal_rcc_ex.h"
#include "stm32wbxx_ll_hsem.h"
#include "stm32wbxx_ll_rcc.h"
#if defined(STM32WB55xx)
#include "stm32wb55xx.h"
#elif defined(STM32WB35xx)
#include "stm32wb35xx.h"
#endif
#else
#error                                                                         \
    "Unsupported STM32 family - only F1xx, F4xx, G4xx and WBxx are currently supported"
#endif

#include "Arduino.h"
#include "arduino/Adafruit_TinyUSB_API.h"
#include "tusb.h"

//--------------------------------------------------------------------+
// Forward USB interrupt events to TinyUSB IRQ Handler
//--------------------------------------------------------------------+
extern "C" {

#if defined(STM32F4xx)
void OTG_FS_IRQHandler(void) { tud_int_handler(0); }

#elif defined(STM32F1xx)
void USB_LP_CAN1_RX0_IRQHandler(void) { tud_int_handler(0); }

#elif defined(STM32G4xx)
// Guard against duplicate symbol from the core USBDevice library (usbd_conf.c).
// The boards.txt TinyUSB menu entry must set build.enable_usb= (empty) to stop
// the core USB stack compiling. This #ifndef is a belt-and-braces backup.
#ifndef USBCON
void USB_LP_IRQHandler(void) { tud_int_handler(0); }
void USB_HP_IRQHandler(void) { tud_int_handler(0); }
void USBWakeUp_IRQHandler(void) { tud_int_handler(0); }
#endif // USBCON

#elif defined(STM32WBxx)
#ifndef USBCON
void USB_LP_IRQHandler(void) { tud_int_handler(0); }
void USB_HP_IRQHandler(void) { tud_int_handler(0); }
#endif // USBCON

#endif // MCU family IRQ handlers

// tud_task() must be called regularly to service USB events. On STM32 the
// Arduino core does NOT call yield() at the bottom of loop(), so we cannot
// rely on yield() alone. Instead we hook HAL_IncTick() which is called every
// 1 ms from the SysTick ISR — giving us a reliable 1 kHz task pump.
//
// IMPORTANT: tud_task() must NOT be called from within an ISR context.
// We therefore only set a flag inside the ISR-called HAL_IncTick(), and
// do the actual work inside yield() which runs in thread context.
//
// yield() is still provided because it is called by delay() and gives us
// a thread-context opportunity to service the flag immediately, rather than
// waiting for the next loop() iteration. TINYUSB_NEED_POLLING_TASK is NOT
// defined — sketches do not need a manual TinyUSBDevice.task() call.

static volatile bool _tusb_task_pending = false;

void yield(void) {
  if (_tusb_task_pending) {
    _tusb_task_pending = false;
    tud_task();
    if (tud_cdc_connected()) {
      tud_cdc_write_flush();
    }
  }
}

} // extern "C"

// serialEventRun() is called by the STM32 Arduino core after every loop()
// iteration (it is the standard Arduino hook for background processing).
// This guarantees tud_task() runs even in tight loops that never call delay().
void serialEventRun(void) { yield(); }

// Hook into the HAL SysTick callback instead of overriding HAL_IncTick().
// This preserves the HAL's own tick behaviour (including configurable
// tick frequency) while still giving us a reliable 1 ms-ish pump for
// tud_task().
extern "C" void HAL_SYSTICK_Callback(void) { _tusb_task_pending = true; }

//--------------------------------------------------------------------+
// Automatic initialisation via initVariant()
//
// The STM32 Arduino core calls initVariant() from main() after clocks
// and HAL are ready, but BEFORE setup() runs.
//
// TinyUSB_Device_Init(0) configures the hardware and calls tud_init().
// D+ goes high and the host begins enumeration (~500 ms window).
// tud_task() is serviced every 1 ms via HAL_IncTick() → _tusb_task_pending
// → yield(), ensuring host setup packets are answered even if the sketch
// never calls delay() or TinyUSBDevice.task().
//
// NOTE: We do NOT wait for tud_mounted() here. Doing so would block until
// enumeration completes, meaning any MIDI.begin() or other USB interface
// registrations in setup() would be called too late to appear in the
// descriptor. Let setup() run freely and register all interfaces before
// the host finishes enumeration.
//--------------------------------------------------------------------+
//--------------------------------------------------------------------+
// Initialization
//--------------------------------------------------------------------+
void initVariant(void) { TinyUSB_Device_Init(0); }

//--------------------------------------------------------------------+
// Hardware Initialisation
//--------------------------------------------------------------------+
void TinyUSB_Port_InitDevice(uint8_t rhport) {
  (void)rhport;

#if defined(STM32F4xx)
  //=================================================================
  // STM32F4xx: USB OTG FS Configuration
  //=================================================================

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_USB_OTG_FS_CLK_ENABLE();

  GPIO_InitTypeDef GPIO_InitStruct = {};
  GPIO_InitStruct.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStruct.Alternate = GPIO_AF10_OTG_FS;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  NVIC_SetPriority(OTG_FS_IRQn, 0);
  NVIC_EnableIRQ(OTG_FS_IRQn);

  // Disable VBUS sensing (bus-powered)
  USB_OTG_FS->GCCFG |= USB_OTG_GCCFG_NOVBUSSENS;
  USB_OTG_FS->GCCFG &= ~USB_OTG_GCCFG_VBUSBSEN;
  USB_OTG_FS->GCCFG &= ~USB_OTG_GCCFG_VBUSASEN;

#elif defined(STM32F1xx)
  //=================================================================
  // STM32F1xx: USB FS Device Configuration
  //=================================================================

  // On F1xx PA11/PA12 are automatically taken over by the USB peripheral
  // when its clock is enabled - no GPIO AF configuration needed
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_USB_CLK_ENABLE();

  NVIC_SetPriority(USB_LP_CAN1_RX0_IRQn, 0);
  NVIC_EnableIRQ(USB_LP_CAN1_RX0_IRQn);

#elif defined(STM32G4xx)
  //=================================================================
  // STM32G4xx: USB FS Device Configuration (WeAct G431, Nucleo, etc.)
  //=================================================================

  // ---- Step 1: Enable HSI48 if not already on --------------------
  if (!(RCC->CRRCR & RCC_CRRCR_HSI48ON)) {
    RCC->CRRCR |= RCC_CRRCR_HSI48ON;
    while (!(RCC->CRRCR & RCC_CRRCR_HSI48RDY))
      ;
  }

  // ---- Step 2: Route HSI48 to the USB clock domain ---------------
  RCC_PeriphCLKInitTypeDef periphClk = {};
  periphClk.PeriphClockSelection = RCC_PERIPHCLK_USB;
  periphClk.UsbClockSelection = RCC_USBCLKSOURCE_HSI48;
  HAL_RCCEx_PeriphCLKConfig(&periphClk);

  // ---- Step 3: Enable CRS for HSI48 auto-trimming via USB SOF ----
  __HAL_RCC_CRS_CLK_ENABLE();
  RCC_CRSInitTypeDef crs = {};
  crs.Prescaler = RCC_CRS_SYNC_DIV1;
  crs.Source = RCC_CRS_SYNC_SOURCE_USB;
  crs.Polarity = RCC_CRS_SYNC_POLARITY_RISING;
  crs.ReloadValue = __HAL_RCC_CRS_RELOADVALUE_CALCULATE(48000000, 1000);
  crs.ErrorLimitValue = RCC_CRS_ERRORLIMIT_DEFAULT;
  crs.HSI48CalibrationValue = RCC_CRS_HSI48CALIBRATION_DEFAULT;
  HAL_RCCEx_CRSConfig(&crs);

  // ---- Step 4: GPIO and USB peripheral clocks --------------------
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_USB_CLK_ENABLE();

  GPIO_InitTypeDef GPIO_InitStruct = {};
  GPIO_InitStruct.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStruct.Alternate = GPIO_AF10_USB;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  // ---- Step 5: Enable USB interrupts -----------------------------
  NVIC_SetPriority(USB_LP_IRQn, 0);
  NVIC_EnableIRQ(USB_LP_IRQn);
  NVIC_SetPriority(USB_HP_IRQn, 0);
  NVIC_EnableIRQ(USB_HP_IRQn);
  NVIC_SetPriority(USBWakeUp_IRQn, 0);
  NVIC_EnableIRQ(USBWakeUp_IRQn);

  // ---- Step 6: Reset USB peripheral ------------------------------
  __HAL_RCC_USB_FORCE_RESET();
  HAL_Delay(1);
  __HAL_RCC_USB_RELEASE_RESET();

#elif defined(STM32WBxx)
  //=================================================================
  // STM32WBxx: USB FS Device Configuration (WB55, WB35)
  //
  // CPU2 (the RF coprocessor) uses HSI48 to clock its RNG. When CPU2
  // finishes with the RNG it will shut HSI48 down UNLESS CPU1 holds
  // hardware semaphore 5, which is the convention agreed between the
  // two cores for signalling "CPU1 is using HSI48, leave it alone".
  //
  // We acquire semaphore 5 with a 1-step lock BEFORE enabling HSI48.
  // This is a lightweight register write — it does not require the
  // SHCI/IPCC BLE stack machinery at all. CPU2 respects the semaphore
  // regardless of whether a BLE stack is running.
  //
  // HSI48 is trimmed to exactly 48 MHz by the CRS peripheral, which
  // synchronises against USB SOF frames. This is the clock source the
  // WB55 ROM bootloader also expects, so DFU entry is seamless.
  //=================================================================

  // ---- Step 1: Acquire HSEM 5 to prevent CPU2 killing HSI48 -----
  LL_HSEM_1StepLock(HSEM, 5);

  // ---- Step 2: Enable HSI48 and wait for it to be ready ----------
  LL_RCC_HSI48_Enable();
  while (!LL_RCC_HSI48_IsReady())
    ;

  // ---- Step 3: Route HSI48 to the USB clock domain ---------------
  RCC_PeriphCLKInitTypeDef periphClk = {};
  periphClk.PeriphClockSelection = RCC_PERIPHCLK_USB;
  periphClk.UsbClockSelection = RCC_USBCLKSOURCE_HSI48;
  HAL_RCCEx_PeriphCLKConfig(&periphClk);

  // ---- Step 4: Enable CRS for HSI48 auto-trimming via USB SOF ----
  __HAL_RCC_CRS_CLK_ENABLE();
  RCC_CRSInitTypeDef crs = {};
  crs.Prescaler = RCC_CRS_SYNC_DIV1;
  crs.Source = RCC_CRS_SYNC_SOURCE_USB;
  crs.Polarity = RCC_CRS_SYNC_POLARITY_RISING;
  crs.ReloadValue = RCC_CRS_RELOADVALUE_DEFAULT;
  crs.ErrorLimitValue = RCC_CRS_ERRORLIMIT_DEFAULT;
  crs.HSI48CalibrationValue = RCC_CRS_HSI48CALIBRATION_DEFAULT;
  HAL_RCCEx_CRSConfig(&crs);

  // ---- Step 5: Enable VddUSB, GPIO, and USB peripheral clocks ----
  __HAL_RCC_GPIOA_CLK_ENABLE();

  // Enable USB VDD supply — required on WB55 before the USB peripheral
  // will come out of reset
  HAL_PWREx_EnableVddUSB();

  __HAL_RCC_USB_CLK_ENABLE();

  GPIO_InitTypeDef GPIO_InitStruct = {};
  GPIO_InitStruct.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStruct.Alternate = GPIO_AF10_USB;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  // ---- Step 6: Enable USB interrupts -----------------------------
  // WB55 USB IRQs at priority 3 (leave room for BLE stack at 0-2)
  NVIC_SetPriority(USB_LP_IRQn, 3);
  NVIC_EnableIRQ(USB_LP_IRQn);
  NVIC_SetPriority(USB_HP_IRQn, 3);
  NVIC_EnableIRQ(USB_HP_IRQn);

  // ---- Step 7: Reset USB peripheral ------------------------------
  __HAL_RCC_USB_FORCE_RESET();
  HAL_Delay(5);
  __HAL_RCC_USB_RELEASE_RESET();
  HAL_Delay(5);

#endif // MCU family hardware init

  // Initialize TinyUSB device stack (common for all MCUs)
  tud_init(rhport);

#if defined(STM32WBxx)
  // Assert D+ pull-up AFTER tud_init — tud_init resets the USB peripheral
  // which clears BCDR, so setting DPPU before tud_init would have no effect.
  USB->BCDR |= USB_BCDR_DPPU;
#endif

  // Give the peripheral a moment to stabilize before attempting connection
  HAL_Delay(10);
}

//--------------------------------------------------------------------+
// DFU Bootloader Entry
//--------------------------------------------------------------------+
void TinyUSB_Port_EnterDFU(void) {
#if defined(STM32F4xx)
  //=================================================================
  // STM32F4xx: soft-disconnect via DCTL, then direct jump
  //=================================================================
  __HAL_RCC_SYSCFG_CLK_ENABLE();
#if defined(USE_USB_HS)
  USB_OTG_DeviceTypeDef *otg_dev =
      (USB_OTG_DeviceTypeDef *)((uint32_t)USB_OTG_HS + USB_OTG_DEVICE_BASE);
#else
  USB_OTG_DeviceTypeDef *otg_dev =
      (USB_OTG_DeviceTypeDef *)((uint32_t)USB_OTG_FS + USB_OTG_DEVICE_BASE);
#endif
  otg_dev->DCTL |= USB_OTG_DCTL_SDIS;
  HAL_Delay(20);

#elif defined(STM32F1xx)
  //=================================================================
  // STM32F1xx: pull D+ low to signal disconnect, then direct jump
  //=================================================================
  __HAL_RCC_GPIOA_CLK_ENABLE();
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  GPIO_InitStruct.Pin = GPIO_PIN_12;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
  HAL_GPIO_WritePin(GPIOA, GPIO_PIN_12, GPIO_PIN_RESET);
  HAL_Delay(100);

#elif defined(STM32WBxx)
  //=================================================================
  // STM32WBxx: write magic value to RTC BKP0R, then NVIC_SystemReset().
  // dfu_boot_stm32wb.c intercepts the reset in Reset_Handler, reads
  // the magic value, and jumps to the bootloader before SystemInit()
  // runs — giving the bootloader a near-clean chip state.
  //=================================================================

  // Gracefully disconnect USB from host
  USB->BCDR &= ~USB_BCDR_DPPU;
  HAL_Delay(20);

  // Enable PWR clock and unlock backup domain register access
  RCC->APB1ENR1 |= (1u << 28u); // PWREN
  __DSB();
  PWR->CR1 |= (1u << 8u); // DBP — backup domain write enable
  __DSB();

  // Write magic value to survive the reset
  RTC->BKP0R = 0xDEADBEEFu;

  // Trigger clean reset — never returns for WBxx.
  // All code below this point is unreachable on WBxx;
  // it is only executed by F1xx, F4xx, and G4xx.
  NVIC_SystemReset();

#endif

  __disable_irq();

  // Disable USB clock
#if defined(STM32G4xx)
  RCC->APB1ENR1 &= ~(RCC_APB1ENR1_USBEN);
#elif defined(STM32F4xx)
  RCC->AHB2ENR &= ~(RCC_AHB2ENR_OTGFSEN);
#elif defined(STM32F1xx)
  RCC->APB1ENR &= ~(RCC_APB1ENR_USBEN);
#endif

#if defined(STM32WBxx)
  // Unreachable — WBxx exits via NVIC_SystemReset() above.
  // Released here defensively in case of future refactoring.
  LL_HSEM_ReleaseLock(HSEM, 5, 0);
#endif

  // Clear all pending interrupts
  for (uint32_t i = 0; i < 5u; i++) {
    NVIC->ICER[i] = 0xFFFFFFFFu;
    NVIC->ICPR[i] = 0xFFFFFFFFu;
  }

  // Stop SysTick
  SysTick->CTRL = 0u;
  SysTick->LOAD = 0u;
  SysTick->VAL = 0u;

  // Reset clocks to default
#if defined(STM32F4xx)
  // Manual reset — more reliable than HAL_RCC_DeInit on F405
  RCC->CR |= RCC_CR_HSION;
  while (!(RCC->CR & RCC_CR_HSIRDY))
    ;
  RCC->CFGR = 0x00000000;
  while ((RCC->CFGR & RCC_CFGR_SWS) != 0x00)
    ;
  RCC->CR &= ~(RCC_CR_PLLON | RCC_CR_HSEON);
  RCC->PLLCFGR = 0x24003010;
#elif defined(STM32WBxx)
  // Reconfigure USB pins as floating inputs before reset
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  GPIO_InitStruct.Pin = GPIO_PIN_11 | GPIO_PIN_12;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
#else
  HAL_RCC_DeInit();
#endif

  __enable_irq();

  // Remap system memory to 0x00000000 and jump to bootloader
#if defined(STM32G4xx) || defined(STM32F4xx) || defined(STM32WBxx)
  // HAL_Delay(20);
  __HAL_SYSCFG_REMAPMEMORY_SYSTEMFLASH();
#define BOOTLOADER_ADDR 0x1FFF0000u
#elif defined(STM32F1xx)
#define BOOTLOADER_ADDR 0x1FFFF000u
#endif

  typedef void (*BootJump_t)(void);
  const uint32_t sp = *(volatile uint32_t *)BOOTLOADER_ADDR;
  const uint32_t pc = *(volatile uint32_t *)(BOOTLOADER_ADDR + 4u);
  __set_MSP(sp);
  __DSB();
  __ISB();
  ((BootJump_t)pc)();
  while (1) {
  }
}

//--------------------------------------------------------------------+
// Serial Number
//--------------------------------------------------------------------+
uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16]) {
#if defined(STM32F4xx)
  volatile uint32_t *uid = (volatile uint32_t *)0x1FFF7A10;
#elif defined(STM32F1xx)
  volatile uint32_t *uid = (volatile uint32_t *)0x1FFFF7E8;
#elif defined(STM32G4xx) || defined(STM32WBxx)
  volatile uint32_t *uid = (volatile uint32_t *)0x1FFF7590;
#endif

  uint32_t *serial_32 = (uint32_t *)serial_id;
  serial_32[0] = uid[0];
  serial_32[1] = uid[1];
  serial_32[2] = uid[2];
  return 12;
}

#endif // ARDUINO_ARCH_STM32