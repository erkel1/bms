#!/usr/bin/env python3
"""
Battery Auto-Detection Module for BMS
Scans multiple Modbus interfaces to detect connected batteries.
Supports up to 8 interfaces (ports 10001-10008) with up to 32 batteries each (IDs 1-32).
Maximum: 256 batteries across all interfaces.

Uses raw socket Modbus with retries for reliability.
"""

import logging
import socket
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional


@dataclass
class DetectedBattery:
    """Represents a detected battery on a Modbus interface."""
    interface_port: int          # Modbus TCP port (10001-10008)
    slave_id: int                # Modbus slave ID (1-32)
    battery_index: int           # Global battery index
    sensors_per_battery: int     # Number of temperature sensors
    last_seen: float = field(default_factory=time.time)
    is_online: bool = True
    
    @property
    def interface_index(self) -> int:
        return self.interface_port - 10001
    
    def __repr__(self):
        return f"Battery(port={self.interface_port}, id={self.slave_id}, idx={self.battery_index})"


@dataclass
class ModbusInterface:
    """Represents a Modbus interface configuration."""
    host: str
    port: int
    enabled: bool = True
    detected_batteries: List[int] = field(default_factory=list)
    
    @property
    def interface_index(self) -> int:
        return self.port - 10001


def modbus_crc(data):
    """Calculate Modbus CRC-16 checksum."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, 'little')


class BatteryDetector:
    """
    Scans Modbus interfaces to detect connected batteries.
    
    Configuration:
    - Up to 8 interfaces on ports 10001-10008
    - Up to 32 batteries per interface (slave IDs 1-32)
    - Maximum 256 batteries total (8 interfaces x 32 batteries)
    """
    
    MAX_INTERFACES = 8
    MAX_BATTERIES_PER_INTERFACE = 32
    MAX_TOTAL_BATTERIES = 256
    BASE_PORT = 10001
    
    def __init__(self, host: str, interface_ports: List[int], 
                 sensors_per_battery: int = 24,
                 scan_timeout: float = 2.0,
                 test_register: int = 0,
                 test_register_count: int = 1,
                 max_batteries_per_interface: int = 32,
                 retries: int = 3):
        """
        Initialize the battery detector.
        
        Args:
            host: Modbus TCP host IP address
            interface_ports: List of Modbus TCP ports to scan
            sensors_per_battery: Number of temperature sensors per battery
            scan_timeout: Timeout for each Modbus connection attempt
            test_register: Register address to read for detection test
            test_register_count: Number of registers to read for test
            max_batteries_per_interface: Max slave IDs to scan per interface (1-32)
            retries: Number of retries for reliability
        """
        self.host = host
        self.interface_ports = sorted(set(interface_ports))
        self.sensors_per_battery = sensors_per_battery
        self.scan_timeout = scan_timeout
        self.test_register = test_register
        self.test_register_count = test_register_count
        self.max_batteries_per_interface = min(max_batteries_per_interface, self.MAX_BATTERIES_PER_INTERFACE)
        self.retries = retries
        
        # Validate ports
        for port in self.interface_ports:
            if not (self.BASE_PORT <= port < self.BASE_PORT + self.MAX_INTERFACES):
                raise ValueError(f"Invalid interface port {port}. Must be {self.BASE_PORT}-{self.BASE_PORT + self.MAX_INTERFACES - 1}")
        
        self.detected_batteries: List[DetectedBattery] = []
        self.interfaces: Dict[int, ModbusInterface] = {}
        
        for port in self.interface_ports:
            self.interfaces[port] = ModbusInterface(host=host, port=port)
    
    def _test_battery_connection(self, port: int, slave_id: int) -> bool:
        """
        Test if a battery responds at the given port and slave ID.
        Uses retries for reliability.
        """
        for attempt in range(self.retries):
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(self.scan_timeout)
                
                try:
                    sock.connect((self.host, port))
                except socket.error as e:
                    logging.debug(f"Cannot connect to {self.host}:{port}: {e}")
                    continue
                
                try:
                    # Build Modbus query
                    query_base = bytes([slave_id, 3]) + \
                                self.test_register.to_bytes(2, 'big') + \
                                self.test_register_count.to_bytes(2, 'big')
                    query = query_base + modbus_crc(query_base)
                    
                    sock.sendall(query)
                    time.sleep(0.3)  # Wait for response
                    
                    try:
                        response = sock.recv(256)
                    except socket.timeout:
                        continue
                    
                    if not response or len(response) < 5:
                        continue
                    
                    # Verify response
                    if response[0] != slave_id:
                        continue
                    
                    if response[1] & 0x80:  # Error response
                        continue
                    
                    # Verify CRC
                    data_len = response[2] + 3
                    if len(response) >= data_len + 2:
                        received_crc = response[data_len:data_len+2]
                        calculated_crc = modbus_crc(response[:data_len])
                        if received_crc != calculated_crc:
                            continue
                    
                    logging.info(f"Battery detected: port={port}, slave_id={slave_id}")
                    return True
                    
                finally:
                    sock.close()
                    
            except Exception as e:
                logging.debug(f"Error testing battery at {self.host}:{port} slave {slave_id}: {e}")
            
            time.sleep(0.2)  # Wait before retry
        
        return False
    
    def scan_interface(self, port: int) -> List[int]:
        """Scan a single interface for connected batteries."""
        detected_slaves = []
        
        logging.info(f"Scanning interface on port {port} for slave IDs 1-{self.max_batteries_per_interface}...")
        
        for slave_id in range(1, self.max_batteries_per_interface + 1):
            if self._test_battery_connection(port, slave_id):
                detected_slaves.append(slave_id)
                logging.info(f"  Found battery at slave ID {slave_id}")
        
        if port in self.interfaces:
            self.interfaces[port].detected_batteries = detected_slaves
        
        return detected_slaves
    
    def scan_all_interfaces(self) -> List[DetectedBattery]:
        """Scan all configured interfaces for batteries."""
        self.detected_batteries.clear()
        battery_index = 0
        
        logging.info(f"Starting battery detection scan on {len(self.interface_ports)} interface(s)...")
        
        for port in self.interface_ports:
            detected_slaves = self.scan_interface(port)
            
            for slave_id in detected_slaves:
                if battery_index >= self.MAX_TOTAL_BATTERIES:
                    logging.warning(f"Maximum battery limit ({self.MAX_TOTAL_BATTERIES}) reached!")
                    break
                
                battery = DetectedBattery(
                    interface_port=port,
                    slave_id=slave_id,
                    battery_index=battery_index,
                    sensors_per_battery=self.sensors_per_battery
                )
                self.detected_batteries.append(battery)
                battery_index += 1
            
            if battery_index >= self.MAX_TOTAL_BATTERIES:
                break
        
        logging.info(f"Detection complete: Found {len(self.detected_batteries)} battery(ies)")
        for bat in self.detected_batteries:
            logging.info(f"  {bat}")
        
        return self.detected_batteries
    
    def get_battery_by_index(self, index: int) -> Optional[DetectedBattery]:
        """Get a battery by its global index."""
        for bat in self.detected_batteries:
            if bat.battery_index == index:
                return bat
        return None
    
    def get_batteries_on_interface(self, port: int) -> List[DetectedBattery]:
        """Get all batteries on a specific interface."""
        return [bat for bat in self.detected_batteries if bat.interface_port == port]
    
    def get_total_sensor_count(self) -> int:
        """Get total number of temperature sensors across all batteries."""
        return sum(bat.sensors_per_battery for bat in self.detected_batteries)
    
    def refresh_battery_status(self) -> None:
        """Re-check all detected batteries to update online status."""
        for battery in self.detected_batteries:
            is_online = self._test_battery_connection(battery.interface_port, battery.slave_id)
            battery.is_online = is_online
            if is_online:
                battery.last_seen = time.time()
    
    def get_detection_summary(self) -> Dict:
        """Get a summary of detected batteries for logging/display."""
        summary = {
            'total_batteries': len(self.detected_batteries),
            'total_sensors': self.get_total_sensor_count(),
            'interfaces': {}
        }
        
        for port in self.interface_ports:
            batteries = self.get_batteries_on_interface(port)
            summary['interfaces'][port] = {
                'battery_count': len(batteries),
                'slave_ids': [b.slave_id for b in batteries],
                'online_count': sum(1 for b in batteries if b.is_online)
            }
        
        return summary


def create_detector_from_config(settings: dict) -> BatteryDetector:
    """
    Create a BatteryDetector from BMS configuration settings.
    """
    host = settings.get('ip', '192.168.15.248')
    
    # Parse modbus_ports
    ports_config = settings.get('modbus_ports', settings.get('modbus_port', 10001))
    
    if isinstance(ports_config, str):
        ports = [int(p.strip()) for p in ports_config.split(',')]
    elif isinstance(ports_config, (list, tuple)):
        ports = [int(p) for p in ports_config]
    else:
        ports = [int(ports_config)]
    
    # Calculate sensors per battery from config
    num_series_banks = settings.get('num_series_banks', 3)
    sensors_per_bank = settings.get('sensors_per_bank', 8)
    if isinstance(num_series_banks, str):
        num_series_banks = int(num_series_banks)
    if isinstance(sensors_per_bank, str):
        sensors_per_bank = int(sensors_per_bank)
    
    sensors_per_battery = num_series_banks * sensors_per_bank
    timeout = float(settings.get('detection_timeout', 2.0))
    max_per_interface = int(settings.get('max_batteries_per_interface', 32))
    
    return BatteryDetector(
        host=host,
        interface_ports=ports,
        sensors_per_battery=sensors_per_battery,
        scan_timeout=timeout,
        max_batteries_per_interface=max_per_interface,
        retries=3
    )


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    test_settings = {
        'ip': '192.168.15.248',
        'modbus_ports': '10001,10003',
        'num_series_banks': 3,
        'sensors_per_bank': 8,
        'detection_timeout': 2.0,
        'max_batteries_per_interface': 32
    }
    
    detector = create_detector_from_config(test_settings)
    batteries = detector.scan_all_interfaces()
    
    print("\n=== Detection Summary ===")
    summary = detector.get_detection_summary()
    print(f"Total batteries: {summary['total_batteries']}")
    print(f"Total sensors: {summary['total_sensors']}")
    
    for port, info in summary['interfaces'].items():
        print(f"\nInterface port {port}:")
        print(f"  Batteries: {info['battery_count']}")
        print(f"  Slave IDs: {info['slave_ids']}")
