#!/usr/bin/env python3
"""
Custom BMS Battery Driver for Victron Venus OS (Cerbo GX)
=========================================================
Reads battery data from the Raspberry Pi BMS via Modbus TCP
and publishes it as a com.victronenergy.battery service on D-Bus.

This driver ONLY publishes data that the BMS actually measures:
  - Bank voltages (total and per-bank)
  - Temperatures (average, min/max cell)
  - Alarm states (voltage and temperature)
  - Balancing status
  - System topology (banks, parallels)
  - Charge/discharge limits (based on temperature)

Data NOT published (left for SmartShunt or other monitors):
  - State of Charge (SOC) - requires coulomb counting
  - Current - BMS has no current sensor
  - Power - derived from current
  - Consumed Amphours - requires current integration
  - Battery capacity - not measured by BMS

BMS Modbus TCP Register Map (from bms.py on 192.168.15.137:502):
  259:  Total voltage (centivolts, uint16)
  262:  Average temperature (tenths of degrees C, int16)
  268:  Low voltage alarm (0/1/2)
  269:  High voltage alarm (0/1/2)
  273:  Low temperature alarm (0/1/2)
  274:  High temperature alarm (0/1/2)
  1282: State (9=Running, 10=Error, 14=Standby)
  1286: Number of batteries (series banks)
  1287: Batteries parallel
  1288: Batteries series
  1290: Min cell/bank voltage (centivolts)
  1291: Max cell/bank voltage (centivolts)
  1306-1308: Individual bank voltages (centivolts)
"""

import sys
import os
import logging
import time
import platform

# Victron library path
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')

from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from vedbus import VeDbusService

# Modbus TCP client (pymodbus 2.5.3 on Venus OS)
from pymodbus.client.sync import ModbusTcpClient

# --- Configuration ---
BMS_HOST = '192.168.15.137'
BMS_PORT = 502
BMS_UNIT_ID = 1  # Server uses single=True, any unit works
POLL_INTERVAL_MS = 2000  # Poll BMS every 2 seconds
DEVICE_INSTANCE = 1  # D-Bus device instance for this battery
SERVICE_NAME = 'com.victronenergy.battery.modbus_tcp_bms'

# Battery system parameters (from BMS config)
NUM_SERIES_BANKS = 3
# Per-bank thresholds (from battery_monitor.ini)
# Charge/discharge limits as specified for the system
# DVCC limits are read from BMS Modbus registers 305-308 (set in battery_monitor.ini [DVCC])
# Fallback defaults only used if registers can't be read
DVCC_FALLBACK_MAX_CHARGE_VOLTAGE = 61.0
DVCC_FALLBACK_MIN_DISCHARGE_VOLTAGE = 49.5
DVCC_FALLBACK_MAX_CHARGE_CURRENT = 200.0
DVCC_FALLBACK_MAX_DISCHARGE_CURRENT = 200.0

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log = logging.getLogger('dbus-bms-battery')


class BmsBatteryService:
    def __init__(self):
        self._modbus_client = None
        self._connected = False
        self._consecutive_errors = 0
        self._max_errors = 30        # Soft threshold: warn + hold values (60 s)
        self._stale_errors = 150     # Hard threshold: safe fallback + Connected=0 (5 min)

        # Set up D-Bus main loop
        DBusGMainLoop(set_as_default=True)

        # Create VeDbusService (register=False, we register after adding all paths)
        self._dbusservice = VeDbusService(SERVICE_NAME, register=False)

        # Management paths
        self._dbusservice.add_path('/Mgmt/ProcessName', __file__)
        self._dbusservice.add_path('/Mgmt/ProcessVersion', '1.1.0')
        self._dbusservice.add_path('/Mgmt/Connection', f'Modbus TCP {BMS_HOST}:{BMS_PORT}')

        # Mandatory product identification
        self._dbusservice.add_path('/DeviceInstance', DEVICE_INSTANCE)
        self._dbusservice.add_path('/ProductId', 0xFFFF)  # Custom product
        self._dbusservice.add_path('/ProductName', 'BMS Battery Monitor')
        self._dbusservice.add_path('/FirmwareVersion', '1.1')
        self._dbusservice.add_path('/HardwareVersion', '1.0')
        self._dbusservice.add_path('/Serial', 'BMS-MODBUS-TCP-001')
        self._dbusservice.add_path('/Connected', 0)
        self._dbusservice.add_path('/CustomName', 'BMS 3S Battery')

        # Battery DC measurements - ONLY what BMS actually measures
        self._dbusservice.add_path('/Dc/0/Voltage', None)
        self._dbusservice.add_path('/Dc/0/Temperature', None)
        # Current and Power NOT published - BMS has no current sensor
        # SOC NOT published - BMS cannot measure SOC (SmartShunt handles this)

        # Battery state
        self._dbusservice.add_path('/State', None)

        # Charge/Discharge limits for DVCC (based on BMS temperature monitoring)
        self._dbusservice.add_path('/Info/MaxChargeVoltage', DVCC_FALLBACK_MAX_CHARGE_VOLTAGE)
        self._dbusservice.add_path('/Info/MaxChargeCurrent', DVCC_FALLBACK_MAX_CHARGE_CURRENT)
        self._dbusservice.add_path('/Info/MaxDischargeCurrent', DVCC_FALLBACK_MAX_DISCHARGE_CURRENT)
        self._dbusservice.add_path('/Info/BatteryLowVoltage', DVCC_FALLBACK_MIN_DISCHARGE_VOLTAGE)

        # System information - BMS knows topology
        self._dbusservice.add_path('/System/NrOfBatteries', NUM_SERIES_BANKS)
        self._dbusservice.add_path('/System/BatteriesParallel', 8)
        self._dbusservice.add_path('/System/BatteriesSeries', NUM_SERIES_BANKS)
        self._dbusservice.add_path('/System/NrOfCellsPerBattery', NUM_SERIES_BANKS)
        self._dbusservice.add_path('/System/MinCellVoltage', None)
        self._dbusservice.add_path('/System/MaxCellVoltage', None)
        self._dbusservice.add_path('/System/MinCellTemperature', None)
        self._dbusservice.add_path('/System/MaxCellTemperature', None)
        self._dbusservice.add_path('/System/NrOfModulesOnline', NUM_SERIES_BANKS)
        self._dbusservice.add_path('/System/NrOfModulesOffline', 0)
        self._dbusservice.add_path('/System/NrOfModulesBlockingCharge', 0)
        self._dbusservice.add_path('/System/NrOfModulesBlockingDischarge', 0)

        # Individual cell/bank voltages (Cerbo GUI reads these)
        for i in range(1, NUM_SERIES_BANKS + 1):
            self._dbusservice.add_path(f'/Voltages/Cell{i}', None)
        self._dbusservice.add_path('/Voltages/Sum', None)
        self._dbusservice.add_path('/Voltages/Diff', None)

        # Per-bank temperatures
        for i in range(1, NUM_SERIES_BANKS + 1):
            self._dbusservice.add_path(f'/Temperatures/Cell{i}', None)

        # Cell voltage/temperature IDs - BMS knows which bank has min/max
        self._dbusservice.add_path('/System/MinVoltageCellId', '')
        self._dbusservice.add_path('/System/MaxVoltageCellId', '')
        self._dbusservice.add_path('/System/MinTemperatureCellId', '')
        self._dbusservice.add_path('/System/MaxTemperatureCellId', '')

        # IO control - BMS can restrict charge/discharge based on conditions
        self._dbusservice.add_path('/Io/AllowToCharge', 1)
        self._dbusservice.add_path('/Io/AllowToDischarge', 1)

        # Alarms - BMS monitors these
        self._dbusservice.add_path('/Alarms/Alarm', 0)
        self._dbusservice.add_path('/Alarms/LowVoltage', 0)
        self._dbusservice.add_path('/Alarms/HighVoltage', 0)
        self._dbusservice.add_path('/Alarms/LowTemperature', 0)
        self._dbusservice.add_path('/Alarms/HighTemperature', 0)
        self._dbusservice.add_path('/Alarms/CellImbalance', 0)
        self._dbusservice.add_path('/Alarms/InternalFailure', 0)
        self._dbusservice.add_path('/Alarms/HighChargeTemperature', 0)
        self._dbusservice.add_path('/Alarms/LowChargeTemperature', 0)

        # Balancing - BMS actively manages this
        self._dbusservice.add_path('/Balancing', 0)

        # Register the service on D-Bus
        self._dbusservice.register()
        log.info(f'D-Bus service {SERVICE_NAME} registered (instance {DEVICE_INSTANCE})')

        # Start polling
        GLib.timeout_add(POLL_INTERVAL_MS, self._update)

    def _connect_modbus(self):
        """Connect to the BMS Modbus TCP server."""
        try:
            if self._modbus_client:
                self._modbus_client.close()
            self._modbus_client = ModbusTcpClient(BMS_HOST, port=BMS_PORT, timeout=3)
            result = self._modbus_client.connect()
            if result:
                log.info(f'Connected to BMS at {BMS_HOST}:{BMS_PORT}')
                self._connected = True
                self._consecutive_errors = 0
                return True
            else:
                log.warning(f'Failed to connect to BMS at {BMS_HOST}:{BMS_PORT}')
                self._connected = False
                return False
        except Exception as e:
            log.error(f'Modbus connection error: {e}')
            self._connected = False
            return False

    def _read_register(self, address, count=1):
        """Read holding registers from the BMS."""
        if not self._connected:
            if not self._connect_modbus():
                return None
        try:
            result = self._modbus_client.read_holding_registers(address, count=count, unit=BMS_UNIT_ID)
            if result is None or result.isError():
                log.warning(f'Modbus read error at register {address}: {result}')
                self._connected = False  # Force reconnect on next call
                return None
            return result.registers
        except Exception as e:
            log.warning(f'Modbus read exception at register {address}: {e}')
            self._connected = False
            return None

    def _update(self):
        """Poll BMS and update D-Bus paths with data the BMS actually provides."""
        try:
            # Read voltage and alarm registers in a batch (259-274)
            main_regs = self._read_register(259, count=16)
            if main_regs is None:
                self._consecutive_errors += 1
                errs = self._consecutive_errors
                secs = errs * POLL_INTERVAL_MS // 1000
                if errs >= self._stale_errors:
                    # 5 minutes of no contact — write safe values then go offline
                    # AllowToCharge/Discharge=1 + clear alarms so Cerbo falls back
                    # to its own system charge voltage rather than tripping inverter
                    if self._dbusservice['/Connected'] != 0:
                        log.error(f'BMS unreachable for {secs}s — writing safe fallback, setting Connected=0')
                        self._dbusservice['/Io/AllowToCharge'] = 1
                        self._dbusservice['/Io/AllowToDischarge'] = 1
                        self._dbusservice['/Alarms/LowVoltage'] = 0
                        self._dbusservice['/Alarms/HighVoltage'] = 0
                        self._dbusservice['/Alarms/LowTemperature'] = 0
                        self._dbusservice['/Alarms/HighTemperature'] = 0
                        self._dbusservice['/Alarms/Alarm'] = 0
                        self._dbusservice['/Connected'] = 0
                elif errs == self._max_errors:
                    remaining = (self._stale_errors - errs) * POLL_INTERVAL_MS // 1000
                    log.warning(f'BMS unreachable for {secs}s — holding last known values, '
                                f'hard disconnect in {remaining}s if no recovery')
                elif errs > self._max_errors and errs % 30 == 0:
                    remaining = (self._stale_errors - errs) * POLL_INTERVAL_MS // 1000
                    log.warning(f'BMS still unreachable ({secs}s) — hard disconnect in {remaining}s')
                # /Connected and all D-Bus values unchanged — last known values
                # keep DVCC and inverter/MPPT running during transient outages
                return True  # Keep polling

            self._consecutive_errors = 0
            self._dbusservice['/Connected'] = 1

            # Decode registers the BMS actually provides
            voltage = main_regs[0] / 100.0       # reg 259: total voltage in centivolts
            temperature = main_regs[3] / 10.0     # reg 262: avg temperature in decicelsius
            alarm_low_v = main_regs[9]             # reg 268: low voltage alarm
            alarm_high_v = main_regs[10]           # reg 269: high voltage alarm
            alarm_low_temp = main_regs[14]         # reg 273: low temperature alarm
            alarm_high_temp = main_regs[15]        # reg 274: high temperature alarm

            # Update voltage and temperature - the data BMS actually measures
            self._dbusservice['/Dc/0/Voltage'] = round(voltage, 2)
            self._dbusservice['/Dc/0/Temperature'] = round(temperature, 1)

            # Alarms from BMS
            self._dbusservice['/Alarms/LowVoltage'] = alarm_low_v
            self._dbusservice['/Alarms/HighVoltage'] = alarm_high_v
            self._dbusservice['/Alarms/LowTemperature'] = alarm_low_temp
            self._dbusservice['/Alarms/HighTemperature'] = alarm_high_temp
            any_alarm = max(alarm_low_v, alarm_high_v, alarm_low_temp, alarm_high_temp)
            self._dbusservice['/Alarms/Alarm'] = any_alarm

            # Read state register (1282)
            state_regs = self._read_register(1282, count=1)
            if state_regs:
                self._dbusservice['/State'] = state_regs[0]

            # Read system registers (1286-1291) - topology and cell voltages
            sys_regs = self._read_register(1286, count=6)
            if sys_regs:
                self._dbusservice['/System/NrOfBatteries'] = sys_regs[0]
                self._dbusservice['/System/BatteriesParallel'] = sys_regs[1]
                self._dbusservice['/System/BatteriesSeries'] = sys_regs[2]
                self._dbusservice['/System/NrOfCellsPerBattery'] = NUM_SERIES_BANKS  # 3 banks = 3 cells in series
                min_cell_v = sys_regs[4] / 100.0  # reg 1290: min bank voltage
                max_cell_v = sys_regs[5] / 100.0  # reg 1291: max bank voltage
                self._dbusservice['/System/MinCellVoltage'] = round(min_cell_v, 3)
                self._dbusservice['/System/MaxCellVoltage'] = round(max_cell_v, 3)

                # Cell imbalance alarm based on voltage spread between banks
                cell_spread = max_cell_v - min_cell_v
                self._dbusservice['/Alarms/CellImbalance'] = 2 if cell_spread > 0.5 else (1 if cell_spread > 0.3 else 0)

            # Read individual bank voltages (1306-1308)
            bank_regs = self._read_register(1306, count=NUM_SERIES_BANKS)
            if bank_regs:
                bank_voltages = [v / 100.0 for v in bank_regs]
                min_bank = min(bank_voltages)
                max_bank = max(bank_voltages)
                min_idx = bank_voltages.index(min_bank) + 1
                max_idx = bank_voltages.index(max_bank) + 1
                self._dbusservice['/System/MinVoltageCellId'] = f'Bank {min_idx}'
                self._dbusservice['/System/MaxVoltageCellId'] = f'Bank {max_idx}'

                # Publish individual cell/bank voltages for Cerbo GUI
                for i, bv in enumerate(bank_voltages):
                    self._dbusservice[f'/Voltages/Cell{i+1}'] = round(bv, 3)
                self._dbusservice['/Voltages/Sum'] = round(sum(bank_voltages), 3)
                self._dbusservice['/Voltages/Diff'] = round(max_bank - min_bank, 3)

            # Read bank temperatures (registers 318-328)
            temp_regs = self._read_register(318, count=11)
            if temp_regs:
                min_cell_temp = temp_regs[0] / 10.0  # reg 318
                max_cell_temp = temp_regs[1] / 10.0  # reg 319
                if min_cell_temp != 0:
                    self._dbusservice['/System/MinCellTemperature'] = round(min_cell_temp, 1)
                if max_cell_temp != 0:
                    self._dbusservice['/System/MaxCellTemperature'] = round(max_cell_temp, 1)
                # Per-bank median temperatures (regs 320-322)
                for i in range(NUM_SERIES_BANKS):
                    bank_temp = temp_regs[2 + i] / 10.0  # regs 320, 321, 322
                    if bank_temp != 0:
                        self._dbusservice[f'/Temperatures/Cell{i+1}'] = round(bank_temp, 1)
                # Find which bank has min/max temp
                bank_medians = [temp_regs[2 + i] / 10.0 for i in range(NUM_SERIES_BANKS)]
                valid_medians = [(t, i) for i, t in enumerate(bank_medians) if t != 0]
                if valid_medians:
                    min_t_bank = min(valid_medians, key=lambda x: x[0])
                    max_t_bank = max(valid_medians, key=lambda x: x[0])
                    self._dbusservice['/System/MinTemperatureCellId'] = f'Bank {min_t_bank[1]+1}'
                    self._dbusservice['/System/MaxTemperatureCellId'] = f'Bank {max_t_bank[1]+1}'

            # Read DVCC limits from BMS (registers 305-308, set in battery_monitor.ini [DVCC])
            dvcc_regs = self._read_register(305, count=4)
            if dvcc_regs:
                max_charge_v = dvcc_regs[0] / 10.0   # reg 305: decivolts
                min_discharge_v = dvcc_regs[1] / 10.0 # reg 306: decivolts
                max_charge_i = dvcc_regs[2] / 10.0    # reg 307: deciamps
                max_discharge_i = dvcc_regs[3] / 10.0  # reg 308: deciamps
                self._dbusservice['/Info/MaxChargeVoltage'] = max_charge_v
                self._dbusservice['/Info/BatteryLowVoltage'] = min_discharge_v
                self._dbusservice['/Info/MaxDischargeCurrent'] = max_discharge_i
            else:
                max_charge_i = DVCC_FALLBACK_MAX_CHARGE_CURRENT

            # Charge/discharge permission based on BMS state
            if state_regs and state_regs[0] == 10:  # Error state
                self._dbusservice['/Io/AllowToCharge'] = 0
                self._dbusservice['/Io/AllowToDischarge'] = 0
            else:
                self._dbusservice['/Io/AllowToCharge'] = 1
                self._dbusservice['/Io/AllowToDischarge'] = 1

            # Adjust charge current limits based on BMS temperature data
            if temperature > 45:
                self._dbusservice['/Info/MaxChargeCurrent'] = 0
                self._dbusservice['/Alarms/HighChargeTemperature'] = 2
            elif temperature > 40:
                self._dbusservice['/Info/MaxChargeCurrent'] = max_charge_i * 0.5
                self._dbusservice['/Alarms/HighChargeTemperature'] = 1
            elif temperature < 0:
                self._dbusservice['/Info/MaxChargeCurrent'] = 0
                self._dbusservice['/Alarms/LowChargeTemperature'] = 2
            elif temperature < 5:
                self._dbusservice['/Info/MaxChargeCurrent'] = max_charge_i * 0.25
                self._dbusservice['/Alarms/LowChargeTemperature'] = 1
            else:
                self._dbusservice['/Info/MaxChargeCurrent'] = max_charge_i
                self._dbusservice['/Alarms/HighChargeTemperature'] = 0
                self._dbusservice['/Alarms/LowChargeTemperature'] = 0

            log.debug(f'Updated: V={voltage:.2f}V T={temperature:.1f}C')

        except Exception as e:
            log.error(f'Update error: {e}')
            import traceback
            traceback.print_exc()

        return True  # Keep the GLib timeout running


def main():
    log.info('Starting BMS Battery D-Bus service v1.3')
    log.info(f'BMS: {BMS_HOST}:{BMS_PORT} unit={BMS_UNIT_ID}')
    log.info(f'Service: {SERVICE_NAME} instance={DEVICE_INSTANCE}')
    log.info('NOTE: SOC/Current/Power NOT published - BMS only provides voltage, temperature, alarms')

    service = BmsBatteryService()

    log.info('Entering GLib main loop')
    mainloop = GLib.MainLoop()
    mainloop.run()


if __name__ == '__main__':
    main()
