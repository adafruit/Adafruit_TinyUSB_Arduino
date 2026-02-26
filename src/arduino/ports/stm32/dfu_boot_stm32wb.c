#if defined(STM32WBxx)
#include "stm32wbxx.h"

#define DFU_MAGIC_VALUE 0xDEADBEEFu
#define BOOTLOADER_ADDR 0x1FFF0000u

// Force linker to retain the assembly startup's .isr_vector section.
// Overriding the weak Reset_Handler from a .c file can cause the linker
// to skip the assembly startup .o entirely, silently dropping the vector table.
extern uint32_t g_pfnVectors[];
__attribute__((used)) static uint32_t *const _force_isr_vector = g_pfnVectors;

extern void SystemInit(void);
extern void __libc_init_array(void);
extern int main(void);
extern uint32_t _estack;
extern uint32_t _sdata, _edata, _sidata;
extern uint32_t _sbss, _ebss;

static void dfu_boot_check(void) {
  // Enable PWR clock and unlock backup domain register access
  RCC->APB1ENR1 |= (1u << 28u); // PWREN
  __DSB();
  PWR->CR1 |= (1u << 8u); // DBP
  __DSB();

  if (RTC->BKP0R == DFU_MAGIC_VALUE) {
    RTC->BKP0R = 0u;
    typedef void (*BootJump_t)(void);
    uint32_t sp = *(volatile uint32_t *)BOOTLOADER_ADDR;
    uint32_t pc = *(volatile uint32_t *)(BOOTLOADER_ADDR + 4u);
    __set_MSP(sp);
    __DSB();
    __ISB();
    ((BootJump_t)pc)();
    while (1) {
    }
  }
}

// Minimal naked wrapper that sets SP and branches to the C reset handler.
__attribute__((naked, used)) void Reset_Handler(void) {
  __asm volatile("ldr r0, =_estack     \n"
                 "mov sp, r0           \n"
                 "b   Reset_Handler_C  \n");
}

// Standard C reset handler: performs DFU check, system init, and
// data/BSS initialisation before entering main().
static void Reset_Handler_C(void) {
  dfu_boot_check();
  SystemInit();
  {
    uint32_t *src = &_sidata;
    uint32_t *dst = &_sdata;
    while (dst < &_edata) {
      *dst++ = *src++;
    }
  }
  {
    uint32_t *dst = &_sbss;
    while (dst < &_ebss) {
      *dst++ = 0u;
    }
  }
  __libc_init_array();
  main();
  while (1) {
  }
}

#endif // STM32WBxx