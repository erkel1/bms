#!/usr/bin/env python3
"""
Comprehensive Modbus Tool
=========================

A unified interactive tool for Modbus TCP communication with NTC temperature sensors.
Combines functionality from multiple separate scripts into one convenient interface.

Features:
- Basic connectivity testing
- Slave scanning (0-247)
- Register reading (holding/input registers)
- Register writing (single register)
- Raw command sending
- CRC calculation and verification
- Slave ID changing
- Debug mode with detailed output
- Multiple Modbus variants testing

Author: BMS Project
Version: 1.0
"""

import socket
import time
import sys
import argparse
from typing import Optional, Union, List


def modbus_crc(data: bytes) -> bytes:
    """
    Calculate Modbus CRC-16 checksum.
    
    Args:
        data: Raw bytes to calculate CRC for
        
    Returns:
        2-byte CRC in little-endian format
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')


class ModbusConnection:
    """Modbus TCP connection handler"""
    
    def __init__(self, ip: str, port: int, timeout: float = 3.0):
        self.ip = ip
        self.port = port
        self.timeout = timeout
        
    def test_connectivity(self) -> bool:
        """Test basic TCP connectivity"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            sock.close()
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    def scan_slaves(self, start: int = 0, end: int = 10) -> List[int]:
        """
        Scan for active Modbus slaves
        
        Args:
            start: Starting slave address
            end: Ending slave address
            
        Returns:
            List of active slave addresses
        """
        active_slaves = []
        print(f"Scanning for active Modbus slaves ({start}-{end})...")
        
        for slave in range(start, end + 1):
            if self.test_slave(slave):
                active_slaves.append(slave)
                print(f"Slave {slave}: ACTIVE")
            else:
                print(f"Slave {slave}: inactive")
                
        return active_slaves
    
    def test_slave(self, slave_addr: int) -> bool:
        """Test if a specific slave is responding"""
        try:
            # Try reading 24 registers to test slave (same as original tool)
            query_base = bytes([slave_addr, 3, 0, 0, 0, 24])
            crc = modbus_crc(query_base)
            query = query_base + crc
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            sock.send(query)
            time.sleep(0.25)  # Added delay like original
            response = sock.recv(1024)
            sock.close()
            
            return len(response) >= 5 and response[0] == slave_addr and response[1] == 3
        except:
            return False
    
    def read_registers(self, slave_addr: int, function_code: int, 
                      start_addr: int, num_registers: int, 
                      debug: bool = False) -> Union[List[int], str]:
        """
        Read registers from Modbus slave
        
        Args:
            slave_addr: Modbus slave address
            function_code: Function code (3=holding, 4=input)
            start_addr: Starting register address
            num_registers: Number of registers to read
            debug: Enable debug output
            
        Returns:
            List of register values or error string
        """
        # Build query
        query_base = bytes([slave_addr, function_code]) + \
                    start_addr.to_bytes(2, 'big') + \
                    num_registers.to_bytes(2, 'big')
        crc = modbus_crc(query_base)
        query = query_base + crc
        
        if debug:
            print(f"Sending query: {query.hex()}")
            print(f"Query base: {query_base.hex()}, CRC: {crc.hex()}")
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            sock.send(query)
            time.sleep(0.25)
            response = sock.recv(1024)
            sock.close()
            
            if debug:
                print(f"Received response: {response.hex()}")
                print(f"Response length: {len(response)}")
            
            # Validate response
            if len(response) < 5:
                return "Short response"
            if len(response) != 3 + response[2] + 2:
                return "Invalid response length"
            
            calc_crc = modbus_crc(response[:-2])
            if calc_crc != response[-2:]:
                return "CRC mismatch"
            
            slave, func, byte_count = response[0:3]
            if debug:
                print(f"Slave: {slave}, Func: {func}, Byte count: {byte_count}")
            
            if slave != slave_addr or func != function_code or byte_count != num_registers * 2:
                if func & 0x80:
                    exception_code = response[2]
                    return f"Modbus exception code {exception_code}"
                return "Invalid response header"
            
            data = response[3:3 + byte_count]
            registers = []
            for i in range(0, len(data), 2):
                val = int.from_bytes(data[i:i+2], 'big', signed=True)
                registers.append(val)
            
            return registers
            
        except Exception as e:
            return f"Error: {str(e)}"
    
    def write_register(self, slave_addr: int, register_addr: int, 
                      value: int, debug: bool = False) -> bool:
        """
        Write single register to Modbus slave
        
        Args:
            slave_addr: Modbus slave address
            register_addr: Register address to write
            value: Value to write
            debug: Enable debug output
            
        Returns:
            True if successful, False otherwise
        """
        # Build write command
        data = bytes([slave_addr, 0x06]) + \
               register_addr.to_bytes(2, 'big') + \
               value.to_bytes(2, 'big')
        crc = modbus_crc(data)
        command = data + crc
        
        if debug:
            print(f"Sending write command: {command.hex()}")
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            sock.send(command)
            response = sock.recv(1024)
            sock.close()
            
            if debug:
                print(f"Response: {response.hex()}")
            
            if response == command:
                print("Command echoed back - write may have been applied.")
                return True
            else:
                print("Unexpected response.")
                return False
                
        except Exception as e:
            print(f"Write failed: {e}")
            return False
    
    def change_slave_id(self, current_id: int, new_id: int, debug: bool = False) -> bool:
        """
        Change Modbus slave ID using the specific hex command format
        
        Args:
            current_id: Current slave ID
            new_id: New slave ID (1-247)
            debug: Enable debug output
            
        Returns:
            True if successful, False otherwise
        """
        if new_id < 1 or new_id > 247:
            print("Invalid ID. Must be 1-247.")
            return False
        
        # Use the specific hex command format from the original tool
        # Function 0x06 (write single register), Register 0x4908 (100 decimal)
        data = bytes([current_id, 0x06, 0x49, 0x08, 0x00, new_id])
        crc = modbus_crc(data)
        cmd = data + crc
        
        if debug:
            print(f"Sending slave ID change command: {cmd.hex()}")
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            sock.send(cmd)
            response = sock.recv(1024)
            sock.close()
            
            if debug:
                print(f"Response: {response.hex()}")
            
            if response == cmd:
                print("Command echoed back - change may have been applied.")
                return True
            else:
                print("Unexpected response.")
                return False
                
        except Exception as e:
            print(f"Failed to change slave ID: {e}")
            return False
    
    def send_raw_command(self, hex_command: str, debug: bool = False) -> str:
        """
        Send raw Modbus command
        
        Args:
            hex_command: Hex string of command to send
            debug: Enable debug output
            
        Returns:
            Response as hex string or error message
        """
        try:
            command = bytes.fromhex(hex_command.replace(' ', ''))
            if debug:
                print(f"Sending raw command: {command.hex()}")
            
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            sock.send(command)
            response = sock.recv(1024)
            sock.close()
            
            if debug:
                print(f"Received response: {response.hex()}")
                print(f"Response length: {len(response)}")
            
            return response.hex()
            
        except Exception as e:
            return f"Error: {str(e)}"
    
    def send_write_register_command(self, slave_addr: int, register_addr: int, value: int, debug: bool = False) -> str:
        """
        Send specific write register command using the format from original tools
        
        Args:
            slave_addr: Modbus slave address
            register_addr: Register address (e.g., 100 for slave ID)
            value: Value to write
            debug: Enable debug output
            
        Returns:
            Response as hex string or error message
        """
        # Build command similar to original: [slave, func, reg_hi, reg_lo, val_hi, val_lo]
        reg_hi = (register_addr >> 8) & 0xFF
        reg_lo = register_addr & 0xFF
        val_hi = (value >> 8) & 0xFF
        val_lo = value & 0xFF
        
        data = bytes([slave_addr, 0x06, reg_hi, reg_lo, val_hi, val_lo])
        crc = modbus_crc(data)
        command = data + crc
        
        if debug:
            print(f"Sending write command: {command.hex()}")
            print(f"  Slave: {slave_addr}, Func: 0x06, Reg: 0x{register_addr:04X}, Value: 0x{value:04X}")
        
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.connect((self.ip, self.port))
            sock.send(command)
            response = sock.recv(1024)
            sock.close()
            
            if debug:
                print(f"Received response: {response.hex()}")
            
            return response.hex()
            
        except Exception as e:
            return f"Error: {str(e)}"


def read_device_settings(conn: ModbusConnection, slave_addr: int, debug: bool = False):
    """Read common device settings from NTC sensor"""
    print(f"\nReading settings from slave {slave_addr}:")
    
    # Read slave address (register 100)
    result = conn.read_registers(slave_addr, 3, 100, 1, debug)
    if isinstance(result, list) and len(result) > 0:
        print(f"Slave Address: {result[0]}")
    else:
        print(f"Slave Address: {result}")
    
    # Read baud rate code (register 101)
    result = conn.read_registers(slave_addr, 3, 101, 1, debug)
    if isinstance(result, list) and len(result) > 0:
        print(f"Baud Rate Code: {result[0]}")
    else:
        print(f"Baud Rate Code: {result}")
    
    # Read parity & stop bits (register 102)
    result = conn.read_registers(slave_addr, 3, 102, 1, debug)
    if isinstance(result, list) and len(result) > 0:
        print(f"Parity & Stop: {result[0]}")
    else:
        print(f"Parity & Stop: {result}")
    
    # Read power voltage (register 17)
    result = conn.read_registers(slave_addr, 3, 17, 1, debug)
    if isinstance(result, list) and len(result) > 0:
        voltage = result[0] / 100.0
        print(f"Power Voltage: {voltage}V")
    else:
        print(f"Power Voltage: {result}")
    
    # Read board temperature (register 16)
    result = conn.read_registers(slave_addr, 3, 16, 1, debug)
    if isinstance(result, list) and len(result) > 0:
        temp = result[0] / 10.0
        print(f"Board Temperature: {temp}°C")
    else:
        print(f"Board Temperature: {result}")


def try_modbus_variants(conn: ModbusConnection):
    """Try different Modbus function codes and parameters"""
    print("\nTrying different Modbus variants...")
    
    test_cases = [
        (1, 3, 0, 10, "Holding registers"),
        (0, 3, 0, 10, "Holding registers (slave 0)"),
        (1, 4, 0, 10, "Input registers"),
        (0, 4, 0, 10, "Input registers (slave 0)"),
    ]
    
    for slave, func, start, count, description in test_cases:
        print(f"\nTrying: {description}")
        result = conn.read_registers(slave, func, start, count, debug=False)
        print(f"Result: {result}")


def interactive_menu():
    """Main interactive menu"""
    print("\n" + "="*60)
    print("Comprehensive Modbus Tool")
    print("="*60)
    
    # Get connection parameters
    ip = input("Enter Modbus device IP [192.168.15.245]: ").strip()
    if not ip:
        ip = "192.168.15.245"
    
    port = input("Enter Modbus port [10001]: ").strip()
    if not port:
        port = 10001
    else:
        port = int(port)
    
    timeout = input("Enter timeout in seconds [3.0]: ").strip()
    if not timeout:
        timeout = 3.0
    else:
        timeout = float(timeout)
    
    conn = ModbusConnection(ip, port, timeout)
    
    # Test connectivity first
    print(f"\nTesting connectivity to {ip}:{port}...")
    if not conn.test_connectivity():
        print("Failed to connect. Exiting.")
        return
    
    print("Connection successful!")
    
    while True:
        print("\n" + "-"*60)
        print("Main Menu:")
        print("1. Scan for slaves")
        print("2. Read registers")
        print("3. Write register")
        print("4. Change slave ID")
        print("5. Read device settings")
        print("6. Try Modbus variants")
        print("7. Send raw command")
        print("8. Write register (specific format)")
        print("9. CRC calculator")
        print("10. Test connectivity")
        print("0. Exit")
        print("-"*60)
        
        choice = input("Select option: ").strip()
        
        if choice == "1":
            start = input("Start slave address [0]: ").strip() or "0"
            end = input("End slave address [10]: ").strip() or "10"
            active = conn.scan_slaves(int(start), int(end))
            if active:
                print(f"Active slaves found: {active}")
            else:
                print("No active slaves found.")
        
        elif choice == "2":
            slave = input("Slave address [1]: ").strip() or "1"
            func = input("Function code (3=holding, 4=input) [3]: ").strip() or "3"
            start = input("Start register [0]: ").strip() or "0"
            count = input("Number of registers [10]: ").strip() or "10"
            debug = input("Debug mode? (y/n) [n]: ").strip().lower() == 'y'
            
            result = conn.read_registers(int(slave), int(func), int(start), int(count), debug)
            print(f"Result: {result}")
        
        elif choice == "3":
            slave = input("Slave address [1]: ").strip() or "1"
            reg = input("Register address [100]: ").strip() or "100"
            value = input("Value to write [1]: ").strip() or "1"
            debug = input("Debug mode? (y/n) [n]: ").strip().lower() == 'y'
            
            success = conn.write_register(int(slave), int(reg), int(value), debug)
            print(f"Write {'successful' if success else 'failed'}")
        
        elif choice == "4":
            print("\nChanging Slave ID:")
            print("This will change the Modbus slave ID using the specific hex command format.")
            print("Make sure only the target device is connected to avoid changing the wrong device.")
            
            current = input("Current slave ID [1]: ").strip() or "1"
            new_id = input("New slave ID [2]: ").strip() or "2"
            debug = input("Debug mode? (y/n) [n]: ").strip().lower() == 'y'
            
            print(f"\nChanging slave ID from {current} to {new_id}...")
            success = conn.change_slave_id(int(current), int(new_id), debug)
            if success:
                print("Change command sent. Waiting 2 seconds for device to process...")
                time.sleep(2)
                if conn.test_slave(int(new_id)):
                    print(f"✓ Success! Slave ID changed to {new_id}.")
                    # Also verify old ID no longer responds
                    if conn.test_slave(int(current)):
                        print(f"⚠ Warning: Old slave ID {current} still responds. Device may not have changed.")
                    else:
                        print(f"✓ Old slave ID {current} no longer responds.")
                else:
                    print(f"✗ Change may not have taken effect. Device with ID {new_id} not responding.")
                    print("Try again or check device configuration.")
            else:
                print("✗ Failed to send change command.")
        
        elif choice == "5":
            slave = input("Slave address [1]: ").strip() or "1"
            debug = input("Debug mode? (y/n) [n]: ").strip().lower() == 'y'
            read_device_settings(conn, int(slave), debug)
        
        elif choice == "6":
            try_modbus_variants(conn)
        
        elif choice == "7":
            command = input("Enter hex command: ").strip()
            debug = input("Debug mode? (y/n) [n]: ").strip().lower() == 'y'
            response = conn.send_raw_command(command, debug)
            print(f"Response: {response}")
        
        elif choice == "8":
            print("\nWrite Register (Specific Format):")
            print("Common presets:")
            print("  1. Change slave ID to 2 (register 100)")
            print("  2. Change slave ID to 3 (register 100)")
            print("  3. Custom register write")
            
            preset = input("Select preset (1-3) or Enter for custom: ").strip()
            
            if preset == "1":
                slave = input("Current slave address [1]: ").strip() or "1"
                response = conn.send_write_register_command(int(slave), 100, 2, debug=True)
                print(f"Response: {response}")
            elif preset == "2":
                slave = input("Current slave address [1]: ").strip() or "1"
                response = conn.send_write_register_command(int(slave), 100, 3, debug=True)
                print(f"Response: {response}")
            else:
                slave = input("Slave address [1]: ").strip() or "1"
                reg = input("Register address (decimal) [100]: ").strip() or "100"
                value = input("Value to write (decimal) [2]: ").strip() or "2"
                debug = input("Debug mode? (y/n) [y]: ").strip().lower() != 'n'
                
                response = conn.send_write_register_command(int(slave), int(reg), int(value), debug)
                print(f"Response: {response}")
        
        elif choice == "9":
            hex_data = input("Enter hex data: ").strip()
            try:
                data = bytes.fromhex(hex_data.replace(' ', ''))
                crc = modbus_crc(data)
                print(f"CRC: {crc.hex()}")
            except ValueError as e:
                print(f"Invalid hex data: {e}")
        
        elif choice == "10":
            if conn.test_connectivity():
                print("Connection successful!")
            else:
                print("Connection failed!")
        
        elif choice == "0":
            print("Goodbye!")
            break
        
        else:
            print("Invalid choice. Please try again.")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description="Comprehensive Modbus Tool")
    parser.add_argument("--ip", help="Modbus device IP address")
    parser.add_argument("--port", type=int, help="Modbus port (default: 10001)")
    parser.add_argument("--slave", type=int, help="Slave address")
    parser.add_argument("--func", type=int, help="Function code (3=holding, 4=input)")
    parser.add_argument("--start", type=int, help="Start register")
    parser.add_argument("--count", type=int, help="Number of registers")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--interactive", action="store_true", help="Run in interactive mode")
    
    args = parser.parse_args()
    
    if args.interactive or not any([args.ip, args.slave, args.func, args.start, args.count]):
        # Interactive mode
        interactive_menu()
    else:
        # Command line mode
        if not all([args.ip, args.slave, args.func, args.start, args.count]):
            print("Error: --ip, --slave, --func, --start, and --count are required for command line mode")
            parser.print_help()
            return
        
        conn = ModbusConnection(args.ip, args.port or 10001)
        
        if args.slave and args.func and args.start and args.count:
            result = conn.read_registers(args.slave, args.func, args.start, args.count, args.debug)
            print(f"Result: {result}")


if __name__ == "__main__":
    main()