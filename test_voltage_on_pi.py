#!/usr/bin/env python3
"""
ADC Diagnostic Script - Tests voltage sensor readings on the Pi
Run this on the Raspberry Pi: python3 test_voltage_on_pi.py
"""

import time

try:
    import smbus
    HAS_SMBUS = True
except ImportError:
    HAS_SMBUS = False
    print("WARNING: smbus not available")

# Configuration from battery_monitor.ini
VOLTAGE_METER_ADDRESS = 0x49  # ADS1115 ADC
MULTIPLEXER_ADDRESS = 0x70     # TCA9548A mux
I2C_BUS_NUMBER = 1
VOLTAGE_DIVIDER_RATIO = 0.01592  # From INI

def choose_channel(channel):
    """Switch I2C multiplexer to specified channel."""
    if HAS_SMBUS:
        bus.write_byte(MULTIPLEXER_ADDRESS, 1 << channel)
        time.sleep(0.01)

def read_adc_raw():
    """Read raw 16-bit value from ADC."""
    if HAS_SMBUS:
        return bus.read_word_data(VOLTAGE_METER_ADDRESS, 0x00)
    return None

def interpret_raw(raw):
    """Test both byte order interpretations."""
    if raw is None:
        return None, None
    
    # Method 1: Little-endian (no swap)
    le = raw
    
    # Method 2: Big-endian swap
    be = ((raw & 0xFF) << 8) | ((raw >> 8) & 0xFF)
    
    return le, be

def raw_to_voltage(raw, divider=VOLTAGE_DIVIDER_RATIO):
    """Convert raw ADC to voltage using BMS formula."""
    FSR = 6.144
    
    # Handle signed interpretation
    if raw & 0x8000:
        signed = raw - 0x10000
        abs_raw = abs(signed)
    else:
        abs_raw = raw
    
    measured = abs_raw * (FSR / 32767)
    actual = measured / divider
    return actual

def main():
    if not HAS_SMBUS:
        print("Cannot run - smbus not available on this system")
        return
    
    global bus
    try:
        bus = smbus.SMBus(I2C_BUS_NUMBER)
    except Exception as e:
        print(f"Error opening I2C bus: {e}")
        return
    
    print("=" * 60)
    print("ADC Voltage Reading Diagnostic")
    print("=" * 60)
    print(f"Expected voltage per bank: ~17V")
    print(f"Voltage divider ratio: {VOLTAGE_DIVIDER_RATIO}")
    print()
    
    for bank in range(1, 4):  # 3 banks
        print(f"Bank {bank}:")
        choose_channel(bank - 1)
        time.sleep(0.1)
        
        readings = []
        for i in range(5):
            raw = read_adc_raw()
            if raw:
                readings.append(raw)
                print(f"  Sample {i+1}: raw={raw} (0x{raw:04X})")
            time.sleep(0.1)
        
        if readings:
            avg_raw = sum(readings) / len(readings)
            le_raw, be_raw = interpret_raw(int(avg_raw))
            
            vol_le = raw_to_voltage(le_raw)
            vol_be = raw_to_voltage(be_raw)
            
            print(f"  Average raw: {avg_raw:.1f}")
            print(f"  Little-endian interpretation: {vol_le:.2f}V")
            print(f"  Big-endian swap interpretation: {vol_be:.2f}V")
            
            if abs(vol_le - 17) < 2:
                print(f"  --> Use LITTLE-ENDIAN (current code correct)")
            elif abs(vol_be - 17) < 2:
                print(f"  --> Use BIG-ENDIAN SWAP (need to fix code)")
            else:
                print(f"  --> Neither gives ~17V - check wiring/ADC address")
        print()

if __name__ == "__main__":
    main()
