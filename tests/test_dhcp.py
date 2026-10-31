"""Tests for following a controller to a new IP address via DHCP discovery."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import SOURCE_DHCP
from homeassistant.const import CONF_NAME, CONF_PASSWORD, CONF_URL, CONF_VERIFY_SSL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import slugify
from opensprinkler.const import DOMAIN
from pyopensprinkler import OpenSprinklerConnectionError

pytest.importorskip("pytest_homeassistant_custom_component")

try:
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
except ImportError:  # Home Assistant < 2025.1
    from homeassistant.components.dhcp import DhcpServiceInfo

OLD_IP = "192.168.1.10"
NEW_IP = "192.168.1.50"
PASSWORD = "secret"
MAC = "AA:BB:CC:DD:EE:FF"
UNIQUE_ID = slugify(MAC)


class MockController:
    """Mock OpenSprinkler controller."""

    def __init__(self, mac=MAC):
        self.mac_address = mac
        self.refresh = AsyncMock()


@pytest.fixture(scope="module", autouse=True)
def mock_setup_entry():
    """Skip a full integration setup when the entry reloads."""
    import custom_components.opensprinkler as ospi

    with (
        patch.object(ospi, "async_setup_entry", AsyncMock(return_value=True)),
        patch.object(ospi, "async_unload_entry", AsyncMock(return_value=True)),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.opensprinkler.config_flow.async_get_clientsession",
            return_value=MagicMock(),
        ),
    ):
        yield


def mock_entry(hass, url=f"http://{OLD_IP}"):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UNIQUE_ID,
        title="OpenSprinkler",
        data={
            CONF_URL: url,
            CONF_PASSWORD: PASSWORD,
            CONF_NAME: "OpenSprinkler",
            CONF_VERIFY_SSL: True,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def start_dhcp(hass, ip=NEW_IP, mac="aabbccddeeff"):
    return await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_DHCP},
        data=DhcpServiceInfo(ip=ip, hostname="", macaddress=mac),
    )


async def test_dhcp_new_ip_updates_url(hass, enable_custom_integrations):
    """The controller answering at a new IP moves the entry there."""
    entry = mock_entry(hass)
    controller = MockController()

    with patch(
        "custom_components.opensprinkler.config_flow.OpenSprinkler",
        return_value=controller,
    ) as mock_api:
        result = await start_dhcp(hass)
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == f"http://{NEW_IP}"
    assert entry.data[CONF_PASSWORD] == PASSWORD
    assert mock_api.call_args.args[0] == f"http://{NEW_IP}"
    assert mock_api.call_args.args[1] == PASSWORD


async def test_dhcp_keeps_port(hass, enable_custom_integrations):
    """Only the host changes; scheme and port are kept."""
    entry = mock_entry(hass, url=f"http://{OLD_IP}:8080")

    with patch(
        "custom_components.opensprinkler.config_flow.OpenSprinkler",
        return_value=MockController(),
    ):
        await start_dhcp(hass)
        await hass.async_block_till_done()

    assert entry.data[CONF_URL] == f"http://{NEW_IP}:8080"


async def test_dhcp_same_ip_does_nothing(hass, enable_custom_integrations):
    """A sighting at the configured IP does not contact the controller."""
    entry = mock_entry(hass)

    with patch(
        "custom_components.opensprinkler.config_flow.OpenSprinkler"
    ) as mock_api:
        result = await start_dhcp(hass, ip=OLD_IP)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == f"http://{OLD_IP}"
    mock_api.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "http://opensprinkler.lan",
        "https://cloud.openthings.io/forward/v1/abc123",
    ],
)
async def test_dhcp_leaves_hostname_urls_alone(hass, enable_custom_integrations, url):
    """Hostname and cloud URLs are never rewritten to an IP."""
    entry = mock_entry(hass, url=url)

    with patch(
        "custom_components.opensprinkler.config_flow.OpenSprinkler"
    ) as mock_api:
        result = await start_dhcp(hass)

    assert result["type"] is FlowResultType.ABORT
    assert entry.data[CONF_URL] == url
    mock_api.assert_not_called()


async def test_dhcp_new_ip_not_answering(hass, enable_custom_integrations):
    """If the new IP does not answer, the entry is unchanged."""
    entry = mock_entry(hass)
    controller = MockController()
    controller.refresh.side_effect = OpenSprinklerConnectionError

    with patch(
        "custom_components.opensprinkler.config_flow.OpenSprinkler",
        return_value=controller,
    ):
        result = await start_dhcp(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
    assert entry.data[CONF_URL] == f"http://{OLD_IP}"


async def test_dhcp_new_ip_is_other_controller(hass, enable_custom_integrations):
    """A different controller answering at the new IP is rejected."""
    entry = mock_entry(hass)

    with patch(
        "custom_components.opensprinkler.config_flow.OpenSprinkler",
        return_value=MockController(mac="11:22:33:44:55:66"),
    ):
        result = await start_dhcp(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_URL] == f"http://{OLD_IP}"


async def test_dhcp_unknown_mac(hass, enable_custom_integrations):
    """A MAC with no configured entry does not start a setup flow."""
    entry = mock_entry(hass)

    with patch(
        "custom_components.opensprinkler.config_flow.OpenSprinkler"
    ) as mock_api:
        result = await start_dhcp(hass, mac="112233445566")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"
    assert entry.data[CONF_URL] == f"http://{OLD_IP}"
    mock_api.assert_not_called()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("aa_bb_cc_dd_ee_ff", "aa:bb:cc:dd:ee:ff"),
        ("aabbccddeeff", "aa:bb:cc:dd:ee:ff"),
        ("AA:BB:CC:DD:EE:FF", "aa:bb:cc:dd:ee:ff"),
        ("not-a-mac", None),
        (None, None),
    ],
)
def test_normalize_mac(value, expected):
    """MACs from unique IDs, DHCP and the API normalise to one form."""
    from custom_components.opensprinkler import normalize_mac

    assert normalize_mac(value) == expected
