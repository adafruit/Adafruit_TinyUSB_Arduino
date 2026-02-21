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

#if (defined(ARDUINO_ARCH_STM32) || defined(ARDUINO_ARCH_ARDUINO_CORE_STM32)) && CFG_TUD_ENABLED

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
  #define GPIO_AF10_USB 0x0AU  // AF10 = USB FS on STM32G4xx (no named macro in HAL)
#else
  #error "Unsupported STM32 family - only F1xx, F4xx and G4xx are currently supported"
#endif

#include "Arduino.h"
#include "arduino/Adafruit_TinyUSB_API.h"
#include "tusb.h"

//--------------------------------------------------------------------+
// Forward USB interrupt events to TinyUSB IRQ Handler
//--------------------------------------------------------------------+
extern "C" {

#if defined(STM32F4xx)
void OTG_FS_IRQHandler(void)
{
    tud_int_handler(0);
}

#elif defined(STM32F1xx)
void USB_LP_CAN1_RX0_IRQHandler(void)
{
    tud_int_handler(0);
}

#elif defined(STM32G4xx)
// Guard against duplicate symbol from the core USBDevice library (usbd_conf.c).
// The boards.txt TinyUSB menu entry must set build.enable_usb= (empty) to stop
// the core USB stack compiling. This #ifndef is a belt-and-braces backup.
#ifndef USBCON
void USB_LP_IRQHandler(void)
{
    tud_int_handler(0);
}
void USB_HP_IRQHandler(void)
{
    tud_int_handler(0);
}
void USBWakeUp_IRQHandler(void)
{
    tud_int_handler(0);
}
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

void yield(void)
{
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
void serialEventRun(void)
{
    yield();
}

// HAL_IncTick() is called every 1 ms from the SysTick_Handler ISR.
// The STM32 HAL defines it as __weak, so we override it here.
// We call the HAL's own uwTick increment, then set the flag for yield().
extern "C" void HAL_IncTick(void)
{
    // Keep the HAL tick counter running (normally done by the weak default).
    // uwTickFreq (HAL_TickFreqTypeDef) was added in newer HAL versions and is
    // not present in the HAL bundled with the STM32 Arduino core, so we
    // increment by 1 directly — correct for the default 1 ms SysTick period.
    extern __IO uint32_t uwTick;
    uwTick += 1U;

    // Signal yield() to run tud_task() at the next thread-context opportunity.
    _tusb_task_pending = true;
}

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
//--------------------------------------------------------------------+
void initVariant(void)
{
    TinyUSB_Device_Init(0);
}

//--------------------------------------------------------------------+
// Porting API
//--------------------------------------------------------------------+
void TinyUSB_Port_InitDevice(uint8_t rhport)
{
    (void) rhport;

#if defined(STM32F4xx)
    //=================================================================
    // STM32F4xx: USB OTG FS Configuration
    //=================================================================

    // Enable clocks FIRST
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USB_OTG_FS_CLK_ENABLE();

    // Configure USB pins (PA11 = DM, PA12 = DP)
    GPIO_InitTypeDef GPIO_InitStruct = {};
    GPIO_InitStruct.Pin       = GPIO_PIN_11 | GPIO_PIN_12;
    GPIO_InitStruct.Mode      = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull      = GPIO_NOPULL;
    GPIO_InitStruct.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF10_OTG_FS;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    // Enable USB IRQ
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

    // Enable USB IRQ
    NVIC_SetPriority(USB_LP_CAN1_RX0_IRQn, 0);
    NVIC_EnableIRQ(USB_LP_CAN1_RX0_IRQn);

#elif defined(STM32G4xx)
    //=================================================================
    // STM32G4xx: USB FS Device Configuration (WeAct G431, Nucleo, etc.)
    //=================================================================

    // ---- Step 1: Enable HSI48 if not already on --------------------
    // Most Arduino core variants for G4 do NOT enable HSI48 by default.
    // On G4, HSI48 is in the CRRCR register (Clock Recovery RC Register),
    // not the CR register. We use direct register access to avoid
    // disrupting the already-running PLL that feeds SYSCLK.
    if (!(RCC->CRRCR & RCC_CRRCR_HSI48ON)) {
        RCC->CRRCR |= RCC_CRRCR_HSI48ON;           // Turn on HSI48
        while (!(RCC->CRRCR & RCC_CRRCR_HSI48RDY)); // Wait for it to stabilize
    }

    // ---- Step 2: Route HSI48 to the USB clock domain ---------------
    // The USB peripheral mux might default to PLLQ (which would be 170MHz
    // and completely wrong). Force it to HSI48 (48 MHz).
    RCC_PeriphCLKInitTypeDef periphClk = {};
    periphClk.PeriphClockSelection = RCC_PERIPHCLK_USB;
    periphClk.UsbClockSelection    = RCC_USBCLKSOURCE_HSI48;
    HAL_RCCEx_PeriphCLKConfig(&periphClk);

    // ---- Step 3: Enable CRS for HSI48 auto-trimming via USB SOF ----
    // HSI48 alone is ~1% (10000 ppm) accurate. CRS uses USB SOF
    // pulses (every 1 ms) to trim it down to <500 ppm, well within
    // the USB FS spec of ±2500 ppm.
    __HAL_RCC_CRS_CLK_ENABLE();
    RCC_CRSInitTypeDef crs = {};
    crs.Prescaler             = RCC_CRS_SYNC_DIV1;
    crs.Source                = RCC_CRS_SYNC_SOURCE_USB;
    crs.Polarity              = RCC_CRS_SYNC_POLARITY_RISING;
    crs.ReloadValue           = __HAL_RCC_CRS_RELOADVALUE_CALCULATE(48000000, 1000);
    crs.ErrorLimitValue       = RCC_CRS_ERRORLIMIT_DEFAULT;
    crs.HSI48CalibrationValue = RCC_CRS_HSI48CALIBRATION_DEFAULT;
    HAL_RCCEx_CRSConfig(&crs);

    // ---- Step 4: GPIO and USB peripheral clocks --------------------
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USB_CLK_ENABLE();

    // Configure PA11 (DM) and PA12 (DP) as USB alternate function (AF10)
    GPIO_InitTypeDef GPIO_InitStruct = {};
    GPIO_InitStruct.Pin       = GPIO_PIN_11 | GPIO_PIN_12;
    GPIO_InitStruct.Mode      = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull      = GPIO_NOPULL;
    GPIO_InitStruct.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF10_USB; // defined as 0x0AU above
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

    // ---- Step 5: Enable USB interrupts -----------------------------
    NVIC_SetPriority(USB_LP_IRQn, 0);
    NVIC_EnableIRQ(USB_LP_IRQn);
    NVIC_SetPriority(USB_HP_IRQn, 0);
    NVIC_EnableIRQ(USB_HP_IRQn);
    NVIC_SetPriority(USBWakeUp_IRQn, 0);
    NVIC_EnableIRQ(USBWakeUp_IRQn);

    // ---- Step 6: Enable USB peripheral power -----------------------
    // The USB peripheral on G4 has a power domain switch (USBRST).
    // We must explicitly bring the peripheral out of reset.
    __HAL_RCC_USB_FORCE_RESET();
    HAL_Delay(1);
    __HAL_RCC_USB_RELEASE_RESET();

#endif

    // Initialize TinyUSB device stack (common for all MCUs)
    tud_init(rhport);

    // Give the peripheral a moment to stabilize before attempting connection
    HAL_Delay(10);
}

void TinyUSB_Port_EnterDFU(void)
{
    // Reboot into the ST system-memory (ROM) DFU bootloader.
    //
    // This is called by the Adafruit TinyUSB library (Adafruit_USBD_CDC.cpp)
    // when it detects the Arduino IDE "touch 1200" sequence (CDC port opened
    // at 1200 baud then closed).
    //
    // Approach: write the STM32duino magic value (0x515B) into the appropriate
    // backup register, then issue a system reset. The STM32duino core startup
    // code checks this register and jumps to the ST ROM DFU bootloader if the
    // value matches.
    //
    // We disconnect from USB first and give the host time to register the
    // disconnection before resetting, so the IDE knows to start looking for
    // a DFU device rather than waiting for the COM port to reappear.

    tud_disconnect();
    HAL_Delay(100);

    // Enable access to the backup domain
    __HAL_RCC_PWR_CLK_ENABLE();
    HAL_PWR_EnableBkUpAccess();

#if defined(STM32F1xx)
    __HAL_RCC_BKP_CLK_ENABLE();
    BKP->DR1 = 0x515B;
#elif defined(STM32F4xx)
    __HAL_RCC_RTC_ENABLE();
    RTC->BKP4R = 0x515B;
#elif defined(STM32G4xx)
    __HAL_RCC_RTC_ENABLE();
    TAMP->BKP4R = 0x515B;
#endif

    NVIC_SystemReset();
}

uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16])
{
#if defined(STM32F4xx)
    // STM32F4xx UID base address (e.g. F411)
    volatile uint32_t *uid = (volatile uint32_t *)0x1FFF7A10;
#elif defined(STM32F1xx)
    // STM32F1xx UID base address (e.g. F103)
    volatile uint32_t *uid = (volatile uint32_t *)0x1FFFF7E8;
#elif defined(STM32G4xx)
    // STM32G4xx UID base address (e.g. G431, G474)
    volatile uint32_t *uid = (volatile uint32_t *)0x1FFF7590;
#endif

    uint32_t *serial_32 = (uint32_t *)serial_id;
    serial_32[0] = uid[0];
    serial_32[1] = uid[1];
    serial_32[2] = uid[2];
    return 12;
}

#endif // ARDUINO_ARCH_STM32