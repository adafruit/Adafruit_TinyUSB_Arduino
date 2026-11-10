// SPDX-License-Identifier: MIT
// Register-model regression tests, not a substitute for bus/hardware tests.
// Run from the library root with the WCH CH32V4x7 SDK header directory:
/* g++ -std=c++20 -Isrc -Itest/dcd/ch32v4x7 -I<SDK>/SRC/Peripheral/inc
 *   test/dcd/ch32v4x7/test_dcd.cpp -o /tmp/test_ch32v4x7 && /tmp/test_ch32v4x7
 */

// Regression checks must remain active in builds that define NDEBUG.
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <cstdio>
#include <vector>

#define CFG_TUSB_CONFIG_FILE "test_config.h"
#define CH32V4x7
#include "ch32v4x7_usb.h"
#include "device/dcd.h"

// Replace the memory-mapped registers, retaining the SDK's real bit values.
#define USB_CH32V4X7_USBHS_REG_H
struct ControlRegister {
  uint8_t value = 0;
  uint8_t read_only = 0;
  unsigned arms = 0;
  /** @brief Read the modeled control register without side effects. */
  operator uint8_t() const { return value; }
  /** @brief Write control fields while honoring read-only and RW0 status bits.
   *  @param next Software-written register value.
   */
  void operator=(uint8_t next) {
    // DONE is write-zero-to-clear. SETUP_IS and TOG_MATCH are read-only.
    value = (next & ~(0x80 | read_only)) | (value & next & 0x80) |
            (value & read_only);
    if ((value & 3) == 2) {
      arms++;
    }
  }
  /** @brief Apply a masked write with the same status-bit semantics.
   *  @param mask Bits to retain in the software-written value.
   */
  void operator&=(uint8_t mask) { *this = uint8_t(value & mask); }
};

static struct {
  uint32_t CONTROL, INT_EN, INT_FG, DEV_AD, BASE_MODE, WAKE_CTRL, MIS_ST;
  uint32_t FRAME_NO, UEP0_DMA, UEP0_MAX_LEN;
  uint16_t UEP_TX_EN, UEP_RX_EN, UEP_TX_TOG_AUTO, UEP_RX_TOG_AUTO;
  uint16_t UEP_TX_ISO, UEP_RX_ISO;
} registers;
#define USBHSD (&registers)
static ControlRegister tx_ctrl[8], rx_ctrl[8];
static uint16_t tx_len[8], rx_len[8];
static uint32_t tx_dma[8], rx_dma[8], max_len[8];
#define EP_TX_CTRL(ep) (::tx_ctrl[ep])
#define EP_RX_CTRL(ep) (::rx_ctrl[ep])
#define EP_TX_LEN(ep) (::tx_len[ep])
#define EP_RX_LEN(ep) (::rx_len[ep])
#define EP_TX_DMA(ep) (::tx_dma[ep])
#define EP_RX_DMA(ep) (::rx_dma[ep])
#define EP_MAX_LEN(ep) (::max_len[ep])

static bool irq_enabled;
static unsigned irq_disables;
#define USBHS1_IRQn 0
static bool NVIC_GetStatusIRQ(int) { return irq_enabled; }
static void NVIC_DisableIRQ(int) {
  irq_enabled = false;
  irq_disables++;
}
static void NVIC_EnableIRQ(int) { irq_enabled = true; }

#ifndef DRIVER_SOURCE
#define DRIVER_SOURCE "../../../src/arduino/ports/ch32/dcd_ch32v4x7.c"
#endif
#include DRIVER_SOURCE

static std::vector<dcd_event_t> events;
static bool resubmit;
static uint8_t next_buffer[7];
/** @brief Record driver events and optionally simulate immediate resubmission.
 *  @param event Event emitted by the driver under test.
 *  @param in_isr Whether the driver expects the USB interrupt to be masked.
 */
extern "C" void dcd_event_handler(const dcd_event_t *event, bool in_isr) {
  assert(!in_isr || !irq_enabled);
  events.push_back(*event);
  if (resubmit && event->event_id == DCD_EVENT_XFER_COMPLETE) {
    resubmit = false;
    assert(registers.UEP_TX_EN == 3 || registers.UEP_RX_EN == 3);
    dcd_edpt_xfer(0, event->xfer_complete.ep_addr, next_buffer,
                  sizeof(next_buffer), false);
  }
}

/** @brief Initialize the driver and clear modeled hardware and event history.
 */
static void reset_model() {
  registers = {};
  for (unsigned ep = 0; ep < 8; ep++) {
    tx_ctrl[ep] = ControlRegister{};
    rx_ctrl[ep] = ControlRegister{};
    rx_ctrl[ep].read_only = USBHS_UEP_R_TOG_MATCH | USBHS_UEP_R_SETUP_IS;
  }
  irq_enabled = false;
  const bool initialized = dcd_init(0, nullptr);
  assert(initialized);
  registers.INT_FG = 0;
  events.clear();
  resubmit = false;
  irq_disables = 0;
}

/** @brief Service a modeled transfer interrupt, preserving its enable state. */
static void interrupt() {
  bool enabled = irq_enabled;
  irq_enabled = false;
  registers.INT_FG = USBHS_UDIF_TRANSFER;
  dcd_int_handler(0);
  registers.INT_FG = 0;
  irq_enabled = enabled;
}

/** @brief Deliver a SETUP packet through the driver's interrupt handler.
 *  @param direction Direction of the request's data stage.
 *  @param length Requested data length in bytes.
 */
static void setup_request(uint8_t direction, uint16_t length) {
  tusb_control_request_t setup = {};
  setup.bmRequestType = direction << 7;
  setup.bRequest = TUSB_REQ_GET_DESCRIPTOR;
  setup.wLength = length;
  memcpy(ep0_buffer, &setup, sizeof(setup));
  rx_ctrl[0].value = USBHS_UEP_R_DONE | USBHS_UEP_R_SETUP_IS;
  interrupt();
}

/** @brief Check completion ordering and DATA1 after an overlapping SETUP.
 *  @param direction Direction of the old request's data stage.
 *  @param request_len Old request's wLength in bytes.
 *  @param packet_len Length of its completed IN packet, including zero.
 *  @param preserve Whether the old status-IN completion must be delivered.
 */
static void ep0_overlap(uint8_t direction, uint16_t request_len,
                        uint16_t packet_len, bool preserve) {
  reset_model();
  setup_request(direction, request_len);
  uint8_t data[64] = {};
  dcd_edpt_xfer(0, 0x80, data, packet_len, false);
  tx_ctrl[0].value |= USBHS_UEP_T_DONE;
  events.clear();
  setup_request(TUSB_DIR_IN, 128);
  assert(events.size() == (preserve ? 2u : 1u));
  if (preserve) {
    assert(events[0].event_id == DCD_EVENT_XFER_COMPLETE);
    assert(events[0].xfer_complete.len == 0);
  }
  assert(events.back().event_id == DCD_EVENT_SETUP_RECEIVED);
  assert(events.back().setup_received.wLength == 128);
  assert(!xfer_status[0][TUSB_DIR_IN].valid);
  dcd_edpt_xfer(0, 0x80, data, sizeof(data), false);
  assert((tx_ctrl[0].value & USBHS_UEP_T_TOG_MASK) == USBHS_UEP_T_TOG_DATA1);
}

static uint8_t bulk_buffer[1100];
/** @brief Initialize and arm a bulk endpoint with a 512-byte maximum packet.
 *  @param address Endpoint address, including its direction bit.
 *  @param length Submitted transfer length in bytes.
 */
static void open_bulk(uint8_t address, uint16_t length) {
  reset_model();
  tusb_desc_endpoint_t ep = {};
  ep.bEndpointAddress = address;
  ep.bmAttributes.xfer = TUSB_XFER_BULK;
  ep.wMaxPacketSize = 512;
  const bool opened = dcd_edpt_open(0, &ep);
  assert(opened);
  const bool submitted = dcd_edpt_xfer(0, address, bulk_buffer, length, false);
  assert(submitted);
  irq_enabled = true;
}

/** @brief Check IN byte accounting, DATA0 and rearming during clear-halt.
 *  @param done Whether the armed packet has already been acknowledged.
 *  @param length Submitted transfer length in bytes.
 *  @param stalled Whether to halt the endpoint before clearing it.
 */
static void clear_in(bool done, uint16_t length, bool stalled) {
  open_bulk(0x81, length);
  if (stalled) {
    dcd_edpt_stall(0, 0x81);
  }
  if (done) {
    tx_ctrl[1].value |= USBHS_UEP_T_DONE;
  }
  tx_ctrl[1].arms = 0;
  dcd_edpt_clear_stall(0, 0x81);
  auto &xfer = xfer_status[1][TUSB_DIR_IN];
  bool completed = done && length <= 512;
  assert(events.size() == unsigned(completed));
  assert(xfer.valid == !completed);
  assert(!(tx_ctrl[1].value & USBHS_UEP_T_DONE));
  assert((tx_ctrl[1].value & USBHS_UEP_T_TOG_MASK) == USBHS_UEP_T_TOG_DATA0);
  assert(tx_ctrl[1].arms == unsigned(!completed));
  assert(irq_disables == 1 && irq_enabled);
  assert(registers.UEP_TX_EN == 3);
  if (!completed) {
    unsigned offset = done ? 512 : 0;
    assert(tx_dma[1] == uint32_t(uintptr_t(bulk_buffer + offset)));
    assert(xfer.queued_len == offset + TU_MIN(length - offset, 512u));
  } else {
    assert(events[0].xfer_complete.len == length);
  }
}

/** @brief Check that clear-halt retains accepted OUT data exactly once.
 *  @param done Whether the receive-completion flag is pending.
 *  @param match Whether the received packet matches the expected data toggle.
 *  @param packet_len Received packet length in bytes.
 */
static void clear_out(bool done, bool match, uint16_t packet_len) {
  open_bulk(1, 1024);
  rx_len[1] = packet_len;
  rx_ctrl[1].value |=
      (done ? USBHS_UEP_R_DONE : 0) | (match ? USBHS_UEP_R_TOG_MATCH : 0);
  rx_ctrl[1].arms = 0;
  dcd_edpt_clear_stall(0, 1);
  unsigned accepted = done && match ? packet_len : 0;
  bool completed = done && match && packet_len < 512;
  assert(xfer_status[1][TUSB_DIR_OUT].queued_len == accepted);
  assert(events.size() == unsigned(completed));
  assert(rx_ctrl[1].arms == unsigned(!completed));
  assert((rx_ctrl[1].value & USBHS_UEP_R_TOG_MASK) == USBHS_UEP_R_TOG_DATA0);
  assert(!(rx_ctrl[1].value & USBHS_UEP_R_DONE));
  assert(irq_disables == 1 && irq_enabled);
  assert(registers.UEP_RX_EN == 3);
  if (!completed) {
    assert(rx_dma[1] == uint32_t(uintptr_t(bulk_buffer + accepted)));
  } else {
    assert(events[0].xfer_complete.len == accepted);
  }
}

/** @brief Run both regression groups, or select one by its command-line name.
 *  @param argc Number of command-line arguments.
 *  @param argv Optional group name: ep0 or halt.
 *  @return Zero after all selected checks pass; a failed assertion aborts.
 */
int main(int argc, char **argv) {
  if (argc == 1 || strcmp(argv[1], "ep0") == 0) {
    ep0_overlap(TUSB_DIR_IN, 128, 64, false); // abandoned full data chunk
    ep0_overlap(TUSB_DIR_IN, 128, 7, false);  // abandoned short data packet
    ep0_overlap(TUSB_DIR_IN, 128, 0, false);  // data-stage terminating ZLP
    ep0_overlap(TUSB_DIR_OUT, 7, 0, true);    // completed control-write status
    ep0_overlap(TUSB_DIR_OUT, 0, 0, true);    // no-data request status
    ep0_overlap(TUSB_DIR_IN, 0, 0, true);     // wLength=0 always has IN status
    puts("PASS: six EP0 SETUP/completion overlap cases");
  }
  if (argc == 1 || strcmp(argv[1], "halt") == 0) {
    clear_in(true, 1024, false); // ACKed first packet must not be retransmitted
    clear_in(true, 512, false);  // completed last packet must be reported once
    clear_in(true, 7, true);     // completed short packet through a halt
    clear_in(true, 0, true);     // completed ZLP
    clear_in(false, 1024, true); // retain the halted packet
    clear_in(false, 7, false);   // retain a short, unacknowledged packet
    clear_in(false, 0, false);   // retain an unacknowledged ZLP
    clear_out(true, true, 512);  // retain accepted OUT bytes
    clear_out(true, true, 7);    // complete a short OUT transfer
    clear_out(true, false, 512); // do not count a duplicate OUT packet
    clear_out(false, false, 0);  // still-armed OUT transfer
    open_bulk(0x81, 519);
    tx_ctrl[1].value |= USBHS_UEP_T_DONE;
    interrupt(); // 512 bytes ACKed; seven-byte final packet is now armed
    dcd_edpt_stall(0, 0x81);
    dcd_edpt_clear_stall(0, 0x81);
    assert(tx_dma[1] == uint32_t(uintptr_t(bulk_buffer + 512)));
    assert(tx_len[1] == 7);
    tx_ctrl[1].value |= USBHS_UEP_T_DONE;
    interrupt();
    assert(events.size() == 1 && events[0].xfer_complete.len == 519);
    for (uint8_t address : {uint8_t(1), uint8_t(0x81)}) {
      open_bulk(address, 512);
      if (address == 1) {
        rx_len[1] = 512;
        rx_ctrl[1].value |= USBHS_UEP_R_DONE | USBHS_UEP_R_TOG_MATCH;
      } else {
        tx_ctrl[1].value |= USBHS_UEP_T_DONE;
      }
      resubmit = true;
      dcd_edpt_clear_stall(0, address);
      assert(!resubmit && events.size() == 1);
      assert(events[0].xfer_complete.len == 512);
      assert(xfer_status[1][address >> 7].valid);
      assert(xfer_status[1][address >> 7].total_len == 7);
      assert((address == 1 ? rx_dma[1] : tx_dma[1]) ==
             uint32_t(uintptr_t(next_buffer)));
    }
    open_bulk(0x81, 1024);
    irq_enabled = false;
    dcd_edpt_clear_stall(0, 0x81);
    assert(!irq_enabled); // preserve the caller's interrupt state
    puts("PASS: fifteen clear-halt completion/rearm cases");
  }
}
