// Test-only stand-in for Windows' bcryptprimitives.dll so Rust binaries start under old Wine. NOT random.
#![no_std]
#[panic_handler]
fn panic(_: &core::panic::PanicInfo) -> ! { loop {} }
#[no_mangle]
pub unsafe extern "system" fn ProcessPrng(buf: *mut u8, len: usize) -> i32 {
    let mut x: u64 = 0x9E3779B97F4A7C15;
    let mut i = 0;
    while i < len {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
        *buf.add(i) = x as u8;
        i += 1;
    }
    1
}
