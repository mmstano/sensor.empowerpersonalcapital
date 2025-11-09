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

__version__ = '0.3.0'

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
ATTR_INVESTMENT = 'investment'
ATTR_MORTGAGE = 'mortgage'
ATTR_CASH = 'cash'
ATTR_OTHER_ASSET = 'other_asset'
ATTR_OTHER_LIABILITY = 'other_liability'
ATTR_CREDIT = 'credit'
ATTR_LOAN = 'loan'

SCAN_INTERVAL = timedelta(minutes=5)
MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=30)

SENSOR_TYPES = {
    ATTR_INVESTMENT: ['INVESTMENT', '', 'investmentAccountsTotal', 'Investment', False],
    ATTR_MORTGAGE: ['MORTGAGE', '', 'mortgageAccountsTotal', 'Mortgage', True],
    ATTR_CASH: ['BANK', 'Cash', 'cashAccountsTotal', 'Cash', False],
    ATTR_OTHER_ASSET: ['OTHER_ASSETS', '', 'otherAssetAccountsTotal', 'Other Asset', False],
    ATTR_OTHER_LIABILITY: ['OTHER_LIABILITIES', '', 'otherLiabilitiesAccountsTotal', 'Other Liability', True],
    ATTR_CREDIT: ['CREDIT_CARD', '', 'creditCardAccountsTotal', 'Credit', True],
    ATTR_LOAN: ['LOAN', '', 'loanAccountsTotal', 'Loan', True],
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_EMAIL): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Optional(CONF_UNIT_OF_MEASUREMENT, default='USD'): cv.string,
    vol.Optional(CONF_CATEGORIES, default=[]): vol.All(cv.ensure_list, [vol.In(SENSOR_TYPES)]),
})

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
    categories = config[CONF_CATEGORIES] if config[CONF_CATEGORIES] else SENSOR_TYPES.keys()

    sensors = [EmpowerNetWorthSensor(rest_pc, uom)]
    for category in categories:
        sensors.append(EmpowerCategorySensor(hass, rest_pc, uom, category))

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


class EmpowerCategorySensor(Entity):
    """Representation of individual category sensors for Empower Personal Capital."""

    def __init__(self, hass, rest, unit_of_measurement, sensor_type):
        self.hass = hass
        self._rest = rest
        self._productType = SENSOR_TYPES[sensor_type][0]
        self._accountType = SENSOR_TYPES[sensor_type][1]
        self._balanceName = SENSOR_TYPES[sensor_type][2]
        self._name = f'Empower {SENSOR_TYPES[sensor_type][3]}'
        self._inverse_sign = SENSOR_TYPES[sensor_type][4]
        self._state = None
        self._unit_of_measurement = unit_of_measurement

    def update(self):
        self._rest.update()
        data = self._rest.data.json()['spData']
        self._state = format_balance(self._inverse_sign, data.get(self._balanceName, 0.0))
        accounts = data.get('accounts', [])
        self.hass.data[self._productType] = {'accounts': []}

        for account in accounts:
            if ((self._productType == account.get('productType')) or
                (self._accountType == account.get('accountType', ''))) and account.get('closeDate', '') == '':
                self.hass.data[self._productType]['accounts'].append({
                    "name": account.get('name', ''),
                    "firm_name": account.get('firmName', ''),
                    "logo": account.get('logoPath', ''),
                    "balance": format_balance(self._inverse_sign, account.get('balance', 0.0)),
                    "account_type": account.get('accountType', ''),
                    "url": account.get('homeUrl', ''),
                    "currency": account.get('currency', ''),
                    "refreshed": how_long_ago(account.get('lastRefreshed', 0)) + ' ago',
                })

    @property
    def name(self):
        return self._name

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
        return self.hass.data[self._productType]


def how_long_ago(last_epoch):
    elapsed = time.time() - last_epoch
    days = elapsed // 86400
    hours = elapsed // 3600 % 24
    minutes = elapsed // 60 % 60
    if days > 0:
        return f"{int(days)} days"
    if hours > 0:
        return f"{int(hours)} hours"
    return f"{int(minutes)} minutes"


def format_balance(inverse_sign, balance):
    return -1.0 * balance if inverse_sign else balance
