/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

#include <Wire.h>
#include "Adafruit_TinyUSB.h"

#include "I2C_USB_Interface.h"

/* This sketch demonstrates how to use tinyusb vendor interface to implement
 * i2c-tiny-usb adapter to use with Linux
 *
 * Reference:
 * - https://github.com/torvalds/linux/blob/master/drivers/i2c/busses/i2c-tiny-usb.c
 * - https://github.com/harbaum/I2C-Tiny-USB
 *
 * Requirement:
 * - Install i2c-tools with
 *    sudo apt install i2c-tools
 *
 * How to test example:
 * - Compile and flash this sketch on your board with an i2c device, it should enumerated as
 *    ID 1c40:0534 EZPrototypes i2c-tiny-usb interface
 *
 * - Run "i2cdetect -l" to find our bus ID e.g
 *    i2c-8	i2c       	i2c-tiny-usb at bus 003 device 039	I2C adapter
 *
 * - Run "i2cdetect -y 8" to scan for on-board device (8 is the above bus ID)
 *         0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
      00:                         -- -- -- -- -- -- -- --
      10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
      20: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
      30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
      40: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
      50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
      60: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
      70: -- -- -- -- -- -- -- 77

   - You can then interact with sensor using following commands:
     i2cget i2cset i2cdump i2ctransfer or using any driver/tools that work on i2c device.
 */

#define STATUS_IDLE 0
#define STATUS_ACK  1
#define STATUS_NAK  2

I2C_USB_Interface i2c_usb;

// check out I2C_FUNC_* defines
static uint32_t i2c_func = 0x8eff0001;
static uint8_t i2c_state = STATUS_IDLE;
static uint8_t i2c_buf[800];

#define MyWire    Wire
//#define MyWire    Wire1

void setup() {
  // needed to identify as a device for i2c_tiny_usb (EZPrototypes VID/PID)
  TinyUSBDevice.setID(0x1c40, 0x0534);
  i2c_usb.begin();
  MyWire.begin();
}

void loop() {
}

uint16_t i2c_read(uint8_t addr, uint8_t* buf, uint16_t len, bool stop_bit)
{
  uint16_t const rd_count = (uint16_t) MyWire.requestFrom(addr, len, stop_bit);

  i2c_state = (len && !rd_count) ? STATUS_NAK : STATUS_ACK;

  // Serial.printf("I2C Read: addr = 0x%02X, len = %u, rd_count %u bytes, status = %u\r\n", addr, len, rd_count, i2c_state);

  for(uint16_t i = 0; i <rd_count; i++)
  {
    buf[i] = (uint8_t) MyWire.read();
  }

  return rd_count;
}

uint16_t i2c_write(uint8_t addr, uint8_t const* buf, uint16_t len, bool stop_bit)
{
  MyWire.beginTransmission(addr);
  uint16_t wr_count = (uint16_t) MyWire.write(buf, len);
  uint8_t const sts = MyWire.endTransmission(stop_bit);

  i2c_state = (sts == 0) ? STATUS_ACK : STATUS_NAK;

  // Serial.printf("I2C Write: addr = 0x%02X, len = %u, wr_count = %u, status = %u\r\n", addr, len, wr_count, i2c_state);

  return wr_count;
}

bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const* request)
{
  if ( request->bmRequestType_bit.type == TUSB_REQ_TYPE_VENDOR )
  {
    uint8_t const cmd = request->bRequest;

    if ( stage == CONTROL_STAGE_SETUP )
    {
      switch ( cmd )
      {
        case CMD_ECHO:
          // echo
          return tud_control_xfer(rhport, request, (void*) &request->wValue, sizeof(request->wValue));

        case CMD_GET_FUNC:
          // capabilities
          return tud_control_xfer(rhport, request, (void*) &i2c_func, sizeof(i2c_func));

        case CMD_SET_DELAY:
          if ( request->wValue == 0 )
          {
            MyWire.setClock(115200);
          }
          else
          {
            int baudrate = 1000000 / request->wValue;
            if ( baudrate > 400000 ) baudrate = 400000;
            MyWire.setClock(baudrate);
          }
          return tud_control_status(rhport, request);

        case CMD_GET_STATUS:
          return tud_control_xfer(rhport, request, (void*) &i2c_state, sizeof(i2c_state));

        case CMD_I2C_IO:
        case CMD_I2C_IO | CMD_I2C_IO_BEGIN:
        case CMD_I2C_IO | CMD_I2C_IO_END:
        case CMD_I2C_IO | CMD_I2C_IO_BEGIN | CMD_I2C_IO_END:
        {
          uint8_t const addr = (uint8_t) request->wIndex;
          uint16_t const flags = request->wValue;
          uint16_t const len = request->wLength;
          bool const stop_bit = (cmd & CMD_I2C_IO_END) ? true : false;

          if (request->bmRequestType_bit.direction == TUSB_DIR_OUT)
          {
            if (len == 0)
            {
              // zero write: do it here since there will be no data stage for len = 0
              i2c_write(addr, i2c_buf, len, stop_bit);
            }
            return tud_control_xfer(rhport, request, i2c_buf, len);
          }else
          {
            uint16_t const rd_count = i2c_read(addr, i2c_buf, len, stop_bit);
            return tud_control_xfer(rhport, request, rd_count ? i2c_buf : NULL, rd_count);
          }
        }
        break;

        default: return true;
      }
    }
    else if ( stage == CONTROL_STAGE_DATA )
    {
      switch ( cmd )
      {
        case CMD_I2C_IO:
        case CMD_I2C_IO | CMD_I2C_IO_BEGIN:
        case CMD_I2C_IO | CMD_I2C_IO_END:
        case CMD_I2C_IO | CMD_I2C_IO_BEGIN | CMD_I2C_IO_END:
          if (request->bmRequestType_bit.direction == TUSB_DIR_OUT)
          {
            uint8_t const addr = (uint8_t) request->wIndex;
            uint16_t const flags = request->wValue;
            uint16_t const len = request->wLength;
            bool const stop_bit = (cmd & CMD_I2C_IO_END) ? true : false;

            i2c_write(addr, i2c_buf, len, stop_bit);
          }
          return true;

        default: return true;
      }
    }
    else
    {
      // CONTROL_STAGE_STATUS
      return true;
    }
  }

  return false;
}

