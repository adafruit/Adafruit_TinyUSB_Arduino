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

#if CFG_TUD_ENABLED && (defined(ARDUINO_ARCH_CH32) || defined(CH32V20x) ||     \
                        defined(CH32V30x) || defined(CH32V4x7))

#include "Arduino.h"
#include "arduino/Adafruit_USBD_Device.h"

//--------------------------------------------------------------------+
// MACRO TYPEDEF CONSTANT ENUM DECLARATION
//--------------------------------------------------------------------+

//--------------------------------------------------------------------+
// Forward USB interrupt events to TinyUSB IRQ Handler
//--------------------------------------------------------------------+
extern "C" {

#if defined(CH32V4x7)
__attribute__((interrupt)) void USBHS1_IRQHandler(void) { tud_int_handler(0); }

// The board supplies its bootloader's reset convention, if it has one.
void ch32TinyUSBEnterBootloader(void) __attribute__((weak));
#endif

// USBD (fsdev)
#if CFG_TUD_WCH_USBIP_FSDEV
__attribute__((interrupt("WCH-Interrupt-fast"))) void
USB_LP_CAN1_RX0_IRQHandler(void) {
  tud_int_handler(0);
}

__attribute__((interrupt("WCH-Interrupt-fast"))) void
USB_HP_CAN1_TX_IRQHandler(void) {
  tud_int_handler(0);
}

__attribute__((interrupt("WCH-Interrupt-fast"))) void
USBWakeUp_IRQHandler(void) {
  tud_int_handler(0);
}
#endif

// USBFS
#if CFG_TUD_WCH_USBIP_USBFS

#if defined(CH32V10x) || defined(CH32V20x)

#if defined(CH32V10x)
#define USBHDWakeUp_IRQHandler USBWakeUp_IRQHandler
#endif

__attribute__((interrupt("WCH-Interrupt-fast"))) void USBHD_IRQHandler(void) {
  tud_int_handler(0);
}

__attribute__((interrupt("WCH-Interrupt-fast"))) void
USBHDWakeUp_IRQHandler(void) {
  tud_int_handler(0);
}
#endif

#ifdef CH32V30x
__attribute__((interrupt("WCH-Interrupt-fast"))) void OTG_FS_IRQHandler(void) {
  tud_int_handler(0);
}
#endif

#endif

// USBHS
#if CFG_TUD_WCH_USBIP_USBHS
__attribute__((interrupt("WCH-Interrupt-fast"))) void USBHS_IRQHandler(void) {
  tud_int_handler(0);
}
#endif

void yield(void) {
  tud_task();
  if (tud_cdc_connected()) {
    tud_cdc_write_flush();
  }
}
}

//--------------------------------------------------------------------+
// Porting API
//--------------------------------------------------------------------+

/** @brief Configure the selected CH32 USB clocks and initialize the device
 * stack.
 *  @param rhport TinyUSB root-hub port to initialize.
 *  @note CH32V4x7 requires a 25 MHz HSE; initialization stops if its PLL fails
 *        to lock. Other CH32 families retain their existing clock requirements.
 */
void TinyUSB_Port_InitDevice(uint8_t rhport) {
#if defined(CH32V4x7)
  // USBHS1 uses the external crystal independently of the CPU clock.
  RCC->CTLR &= ~RCC_USBHSPLLON;
  RCC_USBHSPLLCLKConfig(RCC_USBHSPLLCLKSource_HSE);
#if HSE_VALUE == 25000000
  RCC_USBHSPLLReferConfig(RCC_USBHSPLLCKREFCLK_25M);
#else
#error "CH32V4x7 USBHS currently requires a 25 MHz external crystal"
#endif
  RCC->CTLR |= RCC_USBHSPLLON;
  uint32_t timeout = 1000000;
  while (!(RCC->CTLR & RCC_USBHSPLLRDY) && --timeout) {
  }
  if (!timeout) {
    return;
  }
  RCC_UTMI1cmd(ENABLE);
  RCC_HBPeriphClockCmd(RCC_HBPeriph_USBHS1, ENABLE);
#endif
#if CFG_TUD_WCH_USBIP_FSDEV || CFG_TUD_WCH_USBIP_USBFS
  // Full speed OTG or FSDev

#if defined(CH32V10x)
  EXTEN->EXTEN_CTR |= EXTEN_USBHD_IO_EN;
  EXTEN->EXTEN_CTR &= ~EXTEN_USB_5V_SEL;

#define RCC_AHBPeriph_OTG_FS RCC_AHBPeriph_USBHD
#endif

  uint8_t usb_div;
  switch (SystemCoreClock) {
#if defined(CH32V20x) || defined(CH32V30x)
  case 48000000:
    usb_div = 0; // div1
    break;
  case 96000000:
    usb_div = 1; // div2
    break;
  case 144000000:
    usb_div = 2; // div3
    break;
#elif defined(CH32V10x)
  case 48000000:
    usb_div = RCC_USBCLKSource_PLLCLK_Div1;
    break;
  case 72000000:
    usb_div = RCC_USBCLKSource_PLLCLK_1Div5;
    break;
#endif
  default:
    return; // unsupported
  }

#if defined(CH32V30x)
  RCC_OTGFSCLKConfig(usb_div);
#else
  RCC_USBCLKConfig(usb_div);
#endif

#if CFG_TUD_WCH_USBIP_FSDEV
  RCC_APB1PeriphClockCmd(RCC_APB1Periph_USB, ENABLE);
#endif

#if CFG_TUD_WCH_USBIP_USBFS
  RCC_AHBPeriphClockCmd(RCC_AHBPeriph_OTG_FS, ENABLE);
#endif
#endif

#if CFG_TUD_WCH_USBIP_USBHS
  // High speed USB: currently require 144MHz HSE, update later
  RCC_USBCLK48MConfig(RCC_USBCLK48MCLKSource_USBPHY);
  RCC_USBHSPLLCLKConfig(RCC_HSBHSPLLCLKSource_HSE);
  RCC_USBHSConfig(RCC_USBPLL_Div2);
  RCC_USBHSPLLCKREFCLKConfig(RCC_USBHSPLLCKREFCLK_4M);
  RCC_USBHSPHYPLLALIVEcmd(ENABLE);
  RCC_AHBPeriphClockCmd(RCC_AHBPeriph_USBHS, ENABLE);
#endif

  const tusb_rhport_init_t rh_init = {
      .role = TUSB_ROLE_DEVICE,
      .speed = TUD_OPT_HIGH_SPEED ? TUSB_SPEED_HIGH : TUSB_SPEED_FULL,
  };
  tusb_init(rhport, &rh_init);
}

/** @brief Request CH32V4x7 bootloader entry through the optional board hook.
 *  @note Does nothing when no supported board hook is present.
 */
void TinyUSB_Port_EnterDFU(void) {
#if defined(CH32V4x7)
  if (ch32TinyUSBEnterBootloader) {
    ch32TinyUSBEnterBootloader();
  }
#endif
  // Reset to Boot ROM
  // Serial1.println("Reset to Boot ROM");
  //__disable_irq();
  // tud_deinit(0);
  //
  // // define function pointer to BOOT ROM address
  // void (*bootloader_entry)(void) = (void (*)(void))0x1FFF8000;
  // bootloader_entry();
  // while(1) {}
}

/** @brief Copy the MCU's unique ID into the USB serial-number buffer.
 *  @param serial_id Output buffer with room for 16 bytes.
 *  @return Number of bytes written: eight on CH32V4x7, twelve on other CH32s.
 *  @note CH32V4x7 uses TinyUF2's byte order to retain its USB serial identity.
 */
uint8_t TinyUSB_Port_GetSerialNumber(uint8_t serial_id[16]) {
  volatile uint32_t *ch32_uuid = ((volatile uint32_t *)0x1FFFF7E8UL);
#if defined(CH32V4x7)
  // TinyUF2 formats each of the two UID words most-significant byte first.
  for (uint8_t word = 0; word < 2; word++) {
    const uint32_t value = ch32_uuid[word];
    for (uint8_t byte = 0; byte < 4; byte++) {
      serial_id[4 * word + byte] = value >> (24 - 8 * byte);
    }
  }
  return 8;
#else
  uint32_t *serial_32 = (uint32_t *)serial_id;
  serial_32[0] = ch32_uuid[0];
  serial_32[1] = ch32_uuid[1];
  serial_32[2] = ch32_uuid[2];

  return 12;
#endif
}

#endif
