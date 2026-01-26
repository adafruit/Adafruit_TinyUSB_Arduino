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
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_rcc.h"
#include "Arduino.h"
#include "arduino/Adafruit_TinyUSB_API.h"
#include "tusb.h"

//--------------------------------------------------------------------+
// Forward USB interrupt events to TinyUSB IRQ Handler
//--------------------------------------------------------------------+
extern "C" {

void OTG_FS_IRQHandler(void)
{
    tud_int_handler(0);
}

void yield(void)
{
    tud_task();
    if (tud_cdc_connected()) {
        tud_cdc_write_flush();
    }
}

} // extern "C"

//--------------------------------------------------------------------+
// Porting API
//--------------------------------------------------------------------+
void TinyUSB_Port_InitDevice(uint8_t rhport)
{
    (void) rhport;

    // Enable clocks FIRST
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USB_OTG_FS_CLK_ENABLE();
    
    // Configure USB pins (PA11 = DM, PA12 = DP)
    GPIO_InitTypeDef GPIO_InitStruct = {};
    GPIO_InitStruct.Pin = GPIO_PIN_11 | GPIO_PIN_12;
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Pull = GPIO_NOPULL;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    GPIO_InitStruct.Alternate = GPIO_AF10_OTG_FS;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);
    
    // Enable USB IRQ
    NVIC_SetPriority(OTG_FS_IRQn, 0);
    NVIC_EnableIRQ(OTG_FS_IRQn);
    
    // Disable VBUS sensing (we're bus-powered, don't need it)
    USB_OTG_FS->GCCFG |= USB_OTG_GCCFG_NOVBUSSENS;
    USB_OTG_FS->GCCFG &= ~USB_OTG_GCCFG_VBUSBSEN;
    USB_OTG_FS->GCCFG &= ~USB_OTG_GCCFG_VBUSASEN;

    // Initialize TinyUSB device stack
    tud_init(rhport);
    
}

void TinyUSB_Port_EnterDFU(void) { 
    // Optional - implement bootloader entry if needed
}

uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16])
{
    volatile uint32_t *uid = (volatile uint32_t *)0x1FFF7A10; // STM32F411 UID base
    uint32_t *serial_32 = (uint32_t *)serial_id;
    serial_32[0] = uid[0];
    serial_32[1] = uid[1];
    serial_32[2] = uid[2];
    return 12;
}

#endif // ARDUINO_ARCH_STM32