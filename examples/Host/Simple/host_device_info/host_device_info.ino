/*********************************************************************
 Adafruit invests time and resources providing this open source code,
 please support Adafruit and open-source hardware by purchasing
 products from Adafruit!

 MIT license, check LICENSE for more information
 Copyright (c) 2019 Ha Thach for Adafruit Industries
 All text above, and the splash screen below must be included in
 any redistribution
*********************************************************************/

/* This example demonstrates use of native controller as host (TinyUSB Host)
 * Note:
 * - For most mcu with only 1 native usb, Serial is not available. We will use Serial1 instead
 *
 * Host example will get device descriptors of attached devices and print it out as follows:
      Device 1: ID 046d:c52f
      Device Descriptor:
        bLength             18
        bDescriptorType     1
        bcdUSB              0200
        bDeviceClass        0
        bDeviceSubClass     0
        bDeviceProtocol     0
        bMaxPacketSize0     8
        idVendor            0x046d
        idProduct           0xc52f
        bcdDevice           2200
        iManufacturer       1     Logitech
        iProduct            2     USB Receiver
        iSerialNumber       0
        bNumConfigurations  1
 *
 */

#include "Adafruit_TinyUSB.h"

#ifndef USE_TINYUSB_HOST
  #error This example requires usb stack configured as host in "Tools -> USB Stack -> Adafruit TinyUSB Host"
#endif

// Language ID: English
#define LANGUAGE_ID 0x0409

typedef struct {
  tusb_desc_device_t desc_device;
  uint16_t manufacturer[32];
  uint16_t product[48];
  uint16_t serial[32];
  bool mounted;
} dev_info_t;

// CFG_TUH_DEVICE_MAX is defined by tusb_config header
dev_info_t dev_info[CFG_TUH_DEVICE_MAX] = { 0 };

Adafruit_USBH_Host USBHost;

//--------------------------------------------------------------------+
// setup() & loop()
//--------------------------------------------------------------------+
void setup() {
  Serial1.begin(115200);
  Serial1.println("TinyUSB Host: Device Info Example");

  // Init USB Host on native controller roothub port0
  USBHost.begin(0);
}

void loop() {
  USBHost.task();
  Serial1.flush();
}

//--------------------------------------------------------------------+
// TinyUSB Host callbacks
//--------------------------------------------------------------------+
void print_device_descriptor(tuh_xfer_t *xfer);

void utf16_to_utf8(uint16_t *temp_buf, size_t buf_len);

void print_lsusb(void) {
  bool no_device = true;
  for (uint8_t daddr = 1; daddr < CFG_TUH_DEVICE_MAX + 1; daddr++) {
    // TODO can use tuh_mounted(daddr), but tinyusb has an bug
    // use local connected flag instead
    dev_info_t *dev = &dev_info[daddr - 1];
    if (dev->mounted) {
      Serial1.printf("Device %u: ID %04x:%04x %s %s\r\n", daddr,
                    dev->desc_device.idVendor, dev->desc_device.idProduct,
                    (char *) dev->manufacturer, (char *) dev->product);

      no_device = false;
    }
  }

  if (no_device) {
    Serial1.println("No device connected (except hub)");
  }
}

// Invoked when device is mounted (configured)
void tuh_mount_cb(uint8_t daddr) {
  Serial1.printf("Device attached, address = %d\r\n", daddr);

  dev_info_t *dev = &dev_info[daddr - 1];
  dev->mounted = true;

  // Get Device Descriptor
  tuh_descriptor_get_device(daddr, &dev->desc_device, 18, print_device_descriptor, 0);
}

/// Invoked when device is unmounted (bus reset/unplugged)
void tuh_umount_cb(uint8_t daddr) {
  Serial1.printf("Device removed, address = %d\r\n", daddr);
  dev_info_t *dev = &dev_info[daddr - 1];
  dev->mounted = false;

  // print device summary
  print_lsusb();
}

void print_device_descriptor(tuh_xfer_t *xfer) {
  if (XFER_RESULT_SUCCESS != xfer->result) {
    Serial1.printf("Failed to get device descriptor\r\n");
    return;
  }

  uint8_t const daddr = xfer->daddr;
  dev_info_t *dev = &dev_info[daddr - 1];
  tusb_desc_device_t *desc = &dev->desc_device;

  Serial1.printf("Device %u: ID %04x:%04x\r\n", daddr, desc->idVendor, desc->idProduct);
  Serial1.printf("Device Descriptor:\r\n");
  Serial1.printf("  bLength             %u\r\n"     , desc->bLength);
  Serial1.printf("  bDescriptorType     %u\r\n"     , desc->bDescriptorType);
  Serial1.printf("  bcdUSB              %04x\r\n"   , desc->bcdUSB);
  Serial1.printf("  bDeviceClass        %u\r\n"     , desc->bDeviceClass);
  Serial1.printf("  bDeviceSubClass     %u\r\n"     , desc->bDeviceSubClass);
  Serial1.printf("  bDeviceProtocol     %u\r\n"     , desc->bDeviceProtocol);
  Serial1.printf("  bMaxPacketSize0     %u\r\n"     , desc->bMaxPacketSize0);
  Serial1.printf("  idVendor            0x%04x\r\n" , desc->idVendor);
  Serial1.printf("  idProduct           0x%04x\r\n" , desc->idProduct);
  Serial1.printf("  bcdDevice           %04x\r\n"   , desc->bcdDevice);

  // Get String descriptor using Sync API
  Serial1.printf("  iManufacturer       %u     ", desc->iManufacturer);
  if (XFER_RESULT_SUCCESS ==
      tuh_descriptor_get_manufacturer_string_sync(daddr, LANGUAGE_ID, dev->manufacturer, sizeof(dev->manufacturer))) {
    utf16_to_utf8(dev->manufacturer, sizeof(dev->manufacturer));
    Serial1.print((char *) dev->manufacturer);
  }
  Serial1.printf("\r\n");

  Serial1.printf("  iProduct            %u     ", desc->iProduct);
  if (XFER_RESULT_SUCCESS ==
      tuh_descriptor_get_product_string_sync(daddr, LANGUAGE_ID, dev->product, sizeof(dev->product))) {
    utf16_to_utf8(dev->product, sizeof(dev->product));
    Serial1.print((char *) dev->product);
  }
  Serial1.printf("\r\n");

  Serial1.printf("  iSerialNumber       %u     ", desc->iSerialNumber);
  if (XFER_RESULT_SUCCESS ==
      tuh_descriptor_get_serial_string_sync(daddr, LANGUAGE_ID, dev->serial, sizeof(dev->serial))) {
    utf16_to_utf8(dev->serial, sizeof(dev->serial));
    Serial1.print((char *) dev->serial);
  }
  Serial1.printf("\r\n");

  Serial1.printf("  bNumConfigurations  %u\r\n", desc->bNumConfigurations);

  // print device summary
  print_lsusb();
}

//--------------------------------------------------------------------+
// String Descriptor Helper
//--------------------------------------------------------------------+

// Returns the bytes written, stopping before a unit that would leave no room for the terminating NUL.
static size_t _convert_utf16le_to_utf8(const uint16_t *utf16, size_t utf16_len, uint8_t *utf8, size_t utf8_len) {
  size_t count = 0;
  for (size_t i = 0; i < utf16_len; i++) {
    uint16_t chr = utf16[i];
    size_t chr_bytes = (chr < 0x80) ? 1 : (chr < 0x800) ? 2 : 3;
    if (count + chr_bytes >= utf8_len) {
      break;
    }
    if (chr < 0x80) {
      utf8[count++] = chr & 0xff;
    } else if (chr < 0x800) {
      utf8[count++] = (uint8_t) (0xC0 | (chr >> 6 & 0x1F));
      utf8[count++] = (uint8_t) (0x80 | (chr >> 0 & 0x3F));
    } else {
      // TODO: Verify surrogate.
      utf8[count++] = (uint8_t) (0xE0 | (chr >> 12 & 0x0F));
      utf8[count++] = (uint8_t) (0x80 | (chr >> 6 & 0x3F));
      utf8[count++] = (uint8_t) (0x80 | (chr >> 0 & 0x3F));
    }
    // TODO: Handle UTF-16 code points that take two entries.
  }
  return count;
}

void utf16_to_utf8(uint16_t *temp_buf, size_t buf_len) {
  // bLength is the whole descriptor's, but only buf_len bytes were requested
  size_t desc_len = tu_min16(temp_buf[0] & 0xff, buf_len);
  if (desc_len < 2) {
    temp_buf[0] = 0;
    return;
  }
  size_t utf16_len = (desc_len - 2) / sizeof(uint16_t);

  // UTF-8 can outgrow its UTF-16 source, so convert from a copy; bLength <= 255 bounds it to 126 units
  uint16_t utf16[126];
  memcpy(utf16, temp_buf + 1, utf16_len * sizeof(uint16_t));

  size_t utf8_len = _convert_utf16le_to_utf8(utf16, utf16_len, (uint8_t *) temp_buf, buf_len);
  ((uint8_t *) temp_buf)[utf8_len] = '\0';
}
