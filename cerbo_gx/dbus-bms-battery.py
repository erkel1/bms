#!/usr/bin/env python3
"""
BMS Battery D-Bus driver for Victron Cerbo GX  —  thin bridge v2.0

This driver is intentionally a dumb Modbus-to-D-Bus bridge. ALL resilience
logic (graduated disconnect, reconnect cooldown, safe fallback) lives in
bms.py on the Pi and is expressed via Modbus registers:

  330 = AllowToCharge   (Pi sets 0/1 based on alarm state)
  331 = AllowToDischarge (Pi sets 0/1 based on alarm state)

Local logic here is minimal:
  - Poll every 2s, publish whatever registers say
  - On read failure: keep last D-Bus values (nothing changes)
  - After HARD_DISCONNECT_S seconds of silence: write safe fallback + Connected=0
  - Reconnect: require RECONNECT_READS consecutive good reads before Connected=1
"""

import sys
import logging
sys.path.insert(1, '/opt/victronenergy/dbus-systemcalc-py/ext/velib_python')
from gi.repository import GLib
from vedbus import VeDbusService
from pymodbus.client.sync import ModbusTcpClient

# ── Configuration ─────────────────────────────────────────────────────────────
BMS_HOST             = '192.168.15.202'
BMS_PORT             = 502
BMS_UNIT_ID          = 1
POLL_INTERVAL_MS     = 2000
HARD_DISCONNECT_S    = 1800  # 30 minutes of silence → safe fallback + Connected=0
RECONNECT_READS      = 5     # Consecutive good reads needed after hard disconnect
NUM_BANKS            = 3

SERVICE_NAME         = 'com.victronenergy.battery.modbus_tcp_bms'
DEVICE_INSTANCE      = 1
VERSION              = '2.2'

log = logging.getLogger('dbus-bms-battery')
_HARD_THRESH = HARD_DISCONNECT_S * 1000 // POLL_INTERVAL_MS  # errors before hard disconnect


# ── Service ───────────────────────────────────────────────────────────────────
class BmsBatteryService:

    def __init__(self):
        self._client      = None
        self._mod_ok      = False   # Modbus TCP connection alive
        self._errs        = 0       # Consecutive failed polls
        self._successes   = 0       # Consecutive successful polls
        self._hard_disc   = False   # True once we've set Connected=0

        self._dbus = VeDbusService(SERVICE_NAME, register=False)
        self._init_paths()
        self._dbus.register()
        log.info(f'D-Bus service {SERVICE_NAME} registered (instance {DEVICE_INSTANCE})')
        GLib.timeout_add(POLL_INTERVAL_MS, self._poll)

    # ── D-Bus path setup ──────────────────────────────────────────────────────
    def _init_paths(self):
        d = self._dbus
        d.add_path('/Mgmt/ProcessName',    __file__)
        d.add_path('/Mgmt/ProcessVersion', VERSION)
        d.add_path('/Mgmt/Connection',     f'Modbus TCP {BMS_HOST}:{BMS_PORT}')
        d.add_path('/DeviceInstance',      DEVICE_INSTANCE)
        d.add_path('/ProductName',         'BMS Battery Monitor')
        d.add_path('/ProductId',           0xFFFF)
        d.add_path('/FirmwareVersion',     VERSION)
        d.add_path('/HardwareVersion',     '1.0')
        d.add_path('/Serial',              'BMS-MODBUS-TCP-001')
        d.add_path('/CustomName',          'BMS 3S Battery')

        d.add_path('/Connected',           0)
        d.add_path('/State',               9)
        d.add_path('/Balancing',           0)

        d.add_path('/Dc/0/Voltage',        None)
        d.add_path('/Dc/0/Temperature',    None)

        d.add_path('/Info/MaxChargeVoltage',    None)
        d.add_path('/Info/BatteryLowVoltage',   None)
        d.add_path('/Info/MaxChargeCurrent',    None)
        d.add_path('/Info/MaxDischargeCurrent', None)

        d.add_path('/Io/AllowToCharge',    1)
        d.add_path('/Io/AllowToDischarge', 1)

        for name in ('Alarm', 'LowVoltage', 'HighVoltage',
                     'LowTemperature', 'HighTemperature',
                     'HighChargeTemperature', 'LowChargeTemperature',
                     'CellImbalance', 'InternalFailure'):
            d.add_path(f'/Alarms/{name}', 0)

        d.add_path('/System/NrOfBatteries',               None)
        d.add_path('/System/BatteriesParallel',           None)
        d.add_path('/System/BatteriesSeries',             None)
        d.add_path('/System/NrOfCellsPerBattery',         None)
        d.add_path('/System/NrOfModulesOnline',           None)
        d.add_path('/System/NrOfModulesOffline',          0)
        d.add_path('/System/NrOfModulesBlockingCharge',   0)
        d.add_path('/System/NrOfModulesBlockingDischarge',0)
        d.add_path('/System/MinCellVoltage',              None)
        d.add_path('/System/MaxCellVoltage',              None)
        d.add_path('/System/MinVoltageCellId',            None)
        d.add_path('/System/MaxVoltageCellId',            None)
        d.add_path('/System/MinCellTemperature',          None)
        d.add_path('/System/MaxCellTemperature',          None)
        d.add_path('/System/MinTemperatureCellId',        None)
        d.add_path('/System/MaxTemperatureCellId',        None)

        for i in range(1, NUM_BANKS + 1):
            d.add_path(f'/Voltages/Cell{i}',     None)
            d.add_path(f'/Temperatures/Cell{i}', None)
        d.add_path('/Voltages/Sum',  None)
        d.add_path('/Voltages/Diff', None)

    # ── Modbus helpers ────────────────────────────────────────────────────────
    def _connect(self):
        try:
            if self._client:
                self._client.close()
            self._client = ModbusTcpClient(BMS_HOST, port=BMS_PORT, timeout=3)
            if self._client.connect():
                self._mod_ok = True
                return True
        except Exception as e:
            log.debug(f'Connect error: {e}')
        self._mod_ok = False
        return False

    def _read(self, address, count=1):
        if not self._mod_ok:
            if not self._connect():
                return None
        try:
            r = self._client.read_holding_registers(address, count=count, unit=BMS_UNIT_ID)
            if r is None or r.isError():
                self._mod_ok = False
                return None
            return r.registers
        except Exception:
            self._mod_ok = False
            return None

    # ── Safe fallback ─────────────────────────────────────────────────────────
    def _safe_fallback(self):
        """Write safe values so Venus OS falls back gracefully to system settings."""
        d = self._dbus
        d['/Io/AllowToCharge']    = 1
        d['/Io/AllowToDischarge'] = 1
        d['/Alarms/LowVoltage']       = 0
        d['/Alarms/HighVoltage']      = 0
        d['/Alarms/LowTemperature']   = 0
        d['/Alarms/HighTemperature']  = 0
        d['/Alarms/Alarm']            = 0
        d['/Connected']               = 0
        d['/Dc/0/Voltage']        = None   # clear stale voltage on hard disconnect
        d['/Dc/0/Temperature']    = None

    # ── Poll ──────────────────────────────────────────────────────────────────
    def _poll(self):
        # Primary batch: voltage / temperature / alarms (259-274)
        main = self._read(259, count=16)

        if main is None:
            self._errs      += 1
            self._successes  = 0
            errs  = self._errs
            secs  = errs * POLL_INTERVAL_MS // 1000

            if errs >= _HARD_THRESH and not self._hard_disc:
                log.error(f'BMS unreachable {secs}s — safe fallback, Connected=0')
                self._safe_fallback()
                self._hard_disc = True
            elif errs == 30:
                rem = (_HARD_THRESH - errs) * POLL_INTERVAL_MS // 1000
                log.warning(f'BMS unreachable 60s — holding values, hard disconnect in {rem}s')
            elif errs > 30 and errs % 60 == 0:
                rem = (_HARD_THRESH - errs) * POLL_INTERVAL_MS // 1000
                log.warning(f'BMS unreachable {secs}s — hard disconnect in {rem}s')
            return True

        # ── Successful read ───────────────────────────────────────────────────
        # Register 260 = data-valid flag: 0 means Pi is still booting / has
        # no real data yet. Hold last D-Bus values rather than publishing 0V.
        if main[1] == 0:
            log.debug('Pi starting up (reg 260=0) — holding last D-Bus values')
            return True

        self._errs       = 0
        self._successes += 1

        if self._hard_disc:
            if self._successes >= RECONNECT_READS:
                log.info(f'BMS reconnected after {RECONNECT_READS} stable reads — Connected=1')
                self._hard_disc = False
                self._dbus['/Connected'] = 1
            else:
                log.info(f'BMS recovering: {self._successes}/{RECONNECT_READS} stable reads')
                # Publish values but keep Connected=0 until proven stable
        else:
            self._dbus['/Connected'] = 1

        d = self._dbus

        # Voltage / temperature
        d['/Dc/0/Voltage']     = round(main[0] / 100.0, 2)   # reg 259 centivolts
        d['/Dc/0/Temperature'] = round(main[3] / 10.0,  1)   # reg 262 decicelsius

        # Alarms
        lv = main[9]; hv = main[10]; lt = main[14]; ht = main[15]
        d['/Alarms/LowVoltage']     = lv
        d['/Alarms/HighVoltage']    = hv
        d['/Alarms/LowTemperature'] = lt
        d['/Alarms/HighTemperature']= ht
        d['/Alarms/Alarm']          = max(lv, hv, lt, ht)

        # DVCC limits + AllowToCharge/Discharge from Pi (305-308, 330-331)
        dvcc = self._read(305, count=27)   # 305..331
        if dvcc:
            d['/Info/MaxChargeVoltage']    = dvcc[0]  / 10.0   # 305
            d['/Info/BatteryLowVoltage']   = dvcc[1]  / 10.0   # 306
            d['/Info/MaxChargeCurrent']    = dvcc[2]  / 10.0   # 307
            d['/Info/MaxDischargeCurrent'] = dvcc[3]  / 10.0   # 308
            d['/Io/AllowToCharge']         = dvcc[25]          # 330
            d['/Io/AllowToDischarge']      = dvcc[26]          # 331

        # State (1282)
        st = self._read(1282, count=1)
        if st:
            d['/State'] = st[0]

        # System topology + min/max cell voltage (1286-1291)
        sys_r = self._read(1286, count=6)
        if sys_r:
            n_banks = sys_r[0]
            d['/System/NrOfBatteries']       = n_banks
            d['/System/BatteriesParallel']   = sys_r[1]
            d['/System/BatteriesSeries']     = sys_r[2]
            d['/System/NrOfCellsPerBattery'] = sys_r[2]
            d['/System/NrOfModulesOnline']   = n_banks
            d['/System/MinCellVoltage']      = sys_r[4] / 100.0
            d['/System/MaxCellVoltage']      = sys_r[5] / 100.0

        # Bank voltages (1306..1306+NUM_BANKS-1)
        bv = self._read(1306, count=NUM_BANKS)
        if bv:
            volts = [v / 100.0 for v in bv]
            for i, v in enumerate(volts):
                d[f'/Voltages/Cell{i+1}'] = v
            d['/Voltages/Sum']  = round(sum(volts), 2)
            d['/Voltages/Diff'] = round(max(volts) - min(volts), 3)
            # Cell ID labels
            min_i = volts.index(min(volts))
            max_i = volts.index(max(volts))
            d['/System/MinVoltageCellId'] = f'Bank {min_i + 1}'
            d['/System/MaxVoltageCellId'] = f'Bank {max_i + 1}'

        # Temperatures (318-322: min, max, bank1, bank2, bank3)
        tr = self._read(318, count=5)
        if tr:
            d['/System/MinCellTemperature'] = tr[0] / 10.0
            d['/System/MaxCellTemperature'] = tr[1] / 10.0
            for i in range(NUM_BANKS):
                d[f'/Temperatures/Cell{i+1}'] = tr[2 + i] / 10.0

        return True


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    log.info(f'Starting BMS Battery D-Bus service v{VERSION} (thin bridge)')
    log.info(f'BMS: {BMS_HOST}:{BMS_PORT}  hard-disconnect={HARD_DISCONNECT_S}s  reconnect={RECONNECT_READS} reads')

    from dbus.mainloop.glib import DBusGMainLoop
    DBusGMainLoop(set_as_default=True)
    BmsBatteryService()
    GLib.MainLoop().run()


if __name__ == '__main__':
    main()
