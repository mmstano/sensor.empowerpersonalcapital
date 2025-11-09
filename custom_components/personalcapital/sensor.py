"""
Support for Empower Personal Capital sensors (updated from Personal Capital).

Uses the empower_personal_capital library to fetch account/net worth data.
"""

import logging
import voluptuous as vol
import json
import time
from datetime import timedelta
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.util import Throttle

__version__ = '0.2.0'

REQUIREMENTS = ['empower_personal_capital']

CONF_EMAIL = 'email'
CONF_PASSWORD = 'password'
CONF_UNIT_OF_MEASUREMENT = 'unit_of_measurement'
CONF_CATEGORIES = 'monitored_categories'

SESSION_FILE = '.empower-session'
DATA_EMPOWER = 'empower_cache'

ATTR_NETWORTH = 'networth'
ATTR_ASSETS = 'assets'
ATTR_LIABILITIES = 'liabilities'

SCAN_INTERVAL = timedelta(minutes=5)
MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=30)

SENSOR_TYPES = {
    ATTR_NETWORTH: ['Net Worth'],
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_EMAIL): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default='USD'): cv.string,
    vol.Optional(CONF_CATEGORIES, default=[]): vol.All(cv.ensure_list, [vol.In(SENSOR_TYPES)]),
})

_CONFIGURING = {}
_LOGGER = logging.getLogger(__name__)


def load_session(hass):
    try:
        with open(hass.config.path(SESSION_FILE)) as data_file:
            return json.load(data_file)
    except Exception:
        return {}


def save_session(hass, session):
    with open(hass.config.path(SESSION_FILE), 'w') as data_file:
        json.dump(session, data_file)


def setup_platform(hass, config, add_devices, discovery_info=None):
    """Set up the Empower Personal Capital sensors."""
    from empower_personal_capital import PersonalCapital, RequireTwoFactorException, TwoFactorVerificationModeEnum

    pc = PersonalCapital()
    session = load_session(hass)

    email = config.get(CONF_EMAIL)
    password = config.get(CONF_PASSWORD)

    if session:
        pc.set_session(session)

    try:
        pc.login(email, password)
    except RequireTwoFactorException:
        pc.two_factor_challenge(TwoFactorVerificationModeEnum.SMS)
        code = input("Enter 2FA code: ")
        pc.two_factor_authenticate(TwoFactorVerificationModeEnum.SMS, code)
        pc.authenticate_password(password)

    save_session(hass, pc.get_session())
    rest_pc = EmpowerAccountData(pc)
    uom = config[CONF_UNIT_OF_MEASUREMENT]
    sensors = [EmpowerNetWorthSensor(rest_pc, uom)]
    add_devices(sensors, True)


class EmpowerAccountData(object):
    """Get data from Empower Personal Capital."""

    def __init__(self, pc):
        self._pc = pc
        self.data = None

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def update(self):
        """Fetch latest data from Empower."""
        self.data = self._pc.fetch('/newaccount/getAccounts')
        if not self.data or not self.data.json()['spHeader']['success']:
            _LOGGER.warning("Failed to fetch account data, re-login required.")


class EmpowerNetWorthSensor(Entity):
    """Representation of net worth sensor for Empower Personal Capital."""

    def __init__(self, rest, unit_of_measurement):
        self._rest = rest
        self._unit_of_measurement = unit_of_measurement
        self._state = None
        self._assets = None
        self._liabilities = None
        self.update()

    def update(self):
        """Fetch new state."""
        self._rest.update()
        data = self._rest.data.json()['spData']
        self._state = data.get('networth', 0.0)
        self._assets = data.get('assets', 0.0)
        self._liabilities = data.get('liabilities', 0.0)

    @property
    def name(self):
        return 'Empower Net Worth'

    @property
    def state(self):
        return self._state

    @property
    def unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def icon(self):
        return 'mdi:coin'

    @property
    def device_state_attributes(self):
        return {
            ATTR_ASSETS: self._assets,
            ATTR_LIABILITIES: self._liabilities
        }
