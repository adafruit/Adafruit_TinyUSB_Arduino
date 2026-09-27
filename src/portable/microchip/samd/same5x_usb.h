/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Chris Rabel
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

#ifndef _SAME5X_USB_H_
#define _SAME5X_USB_H_

#if defined(__GNUC__)
#pragma GCC system_header
#endif
#include "tusb_option.h"

#if TU_CHECK_MCU(OPT_MCU_SAME5X)

#ifndef _Ul
#define _Ul(x) x##U
#endif

#ifndef TUSB_SAME5X_COMPAT_ROREG8
#define TUSB_SAME5X_COMPAT_ROREG8 1
typedef volatile const uint8_t RoReg8;
#endif

#if defined(__has_include)
#if __has_include("samd51/include/component/usb.h")
#include "samd51/include/component/usb.h"
#else
#error "SAME5x USB support requires SAMD51 USB component headers on the include path."
#endif
#else
#include "samd51/include/component/usb.h"
#endif

#if !defined(NVMCTRL_SW0) && defined(SW0_ADDR)
#define NVMCTRL_SW0 SW0_ADDR
#elif !defined(NVMCTRL_SW0) && defined(SW0_FUSES_BASE_ADDRESS)
#define NVMCTRL_SW0 SW0_FUSES_BASE_ADDRESS
#endif

#ifndef USB_FUSES_TRANSN_ADDR
#define USB_FUSES_TRANSN_ADDR (NVMCTRL_SW0 + 4)
#define USB_FUSES_TRANSN_Pos 0
#define USB_FUSES_TRANSN_Msk (_Ul(0x1F) << USB_FUSES_TRANSN_Pos)
#endif

#ifndef USB_FUSES_TRANSP_ADDR
#define USB_FUSES_TRANSP_ADDR (NVMCTRL_SW0 + 4)
#define USB_FUSES_TRANSP_Pos 5
#define USB_FUSES_TRANSP_Msk (_Ul(0x1F) << USB_FUSES_TRANSP_Pos)
#endif

#ifndef USB_FUSES_TRIM_ADDR
#define USB_FUSES_TRIM_ADDR (NVMCTRL_SW0 + 4)
#define USB_FUSES_TRIM_Pos 10
#define USB_FUSES_TRIM_Msk (_Ul(0x7) << USB_FUSES_TRIM_Pos)
#endif

#if !defined(USB) && defined(USB_REGS)
#define USB ((Usb *)USB_REGS)
#endif

#endif

#endif
