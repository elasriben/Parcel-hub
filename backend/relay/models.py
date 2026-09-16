"""Domain vocabulary for RELAY: enums, states and the authorized transition map.

The parcel is a controlled state machine, never a freely editable status field.
Every transition is declared here and enforced server-side with an atomic,
conditional DB update (see services.py).
"""
from enum import Enum


class OrgType(str, Enum):
    PLATFORM = "PLATFORM"
    MERCHANT = "MERCHANT"
    CARRIER = "CARRIER"
    RELAY_PARTNER = "RELAY_PARTNER"


class Role(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    OPS_ADMIN = "OPS_ADMIN"
    SUPPORT = "SUPPORT"
    MERCHANT_ADMIN = "MERCHANT_ADMIN"
    MERCHANT_OPERATOR = "MERCHANT_OPERATOR"
    CARRIER_ADMIN = "CARRIER_ADMIN"
    CARRIER_OPERATOR = "CARRIER_OPERATOR"
    RELAY_PARTNER_OWNER = "RELAY_PARTNER_OWNER"
    RELAY_PARTNER_AGENT = "RELAY_PARTNER_AGENT"


PARTNER_ROLES = {Role.RELAY_PARTNER_OWNER.value, Role.RELAY_PARTNER_AGENT.value}


class CustodianType(str, Enum):
    MERCHANT = "MERCHANT"
    CARRIER = "CARRIER"
    RELAY_PARTNER = "RELAY_PARTNER"
    CUSTOMER = "CUSTOMER"


class ParcelState(str, Enum):
    CREATED = "CREATED"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    IN_TRANSIT = "IN_TRANSIT"
    AT_RELAY_POINT = "AT_RELAY_POINT"
    AVAILABLE_FOR_PICKUP = "AVAILABLE_FOR_PICKUP"
    PICKUP_AUTHORIZED = "PICKUP_AUTHORIZED"
    HANDED_OVER = "HANDED_OVER"
    DAMAGED = "DAMAGED"
    INCIDENT = "INCIDENT"
    EXPIRED = "EXPIRED"
    CUSTOMER_REFUSED = "CUSTOMER_REFUSED"
    RETURN_PENDING = "RETURN_PENDING"
    RETURN_READY = "RETURN_READY"
    RETURN_COLLECTED = "RETURN_COLLECTED"
    RETURN_IN_TRANSIT = "RETURN_IN_TRANSIT"
    RETURN_RECEIVED = "RETURN_RECEIVED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"


class PointStatus(str, Enum):
    ACTIVE = "ACTIVE"
    TEMPORARILY_CLOSED = "TEMPORARILY_CLOSED"
    SUSPENDED = "SUSPENDED"
    FULL = "FULL"
    DECOMMISSIONED = "DECOMMISSIONED"


class IncidentType(str, Enum):
    DAMAGED_PACKAGE = "DAMAGED_PACKAGE"
    WRONG_PACKAGE = "WRONG_PACKAGE"
    MISSING_PACKAGE = "MISSING_PACKAGE"
    WRONG_POINT = "WRONG_POINT"
    CUSTOMER_DISPUTE = "CUSTOMER_DISPUTE"
    OTP_FAILURE = "OTP_FAILURE"
    DUPLICATE_SCAN = "DUPLICATE_SCAN"
    PARTNER_ERROR = "PARTNER_ERROR"
    TRANSPORTER_ERROR = "TRANSPORTER_ERROR"
    OTHER = "OTHER"


# States that physically occupy a slot at the relay point (count against capacity).
IN_CUSTODY_STATES = {
    ParcelState.AT_RELAY_POINT.value,
    ParcelState.AVAILABLE_FOR_PICKUP.value,
    ParcelState.PICKUP_AUTHORIZED.value,
    ParcelState.DAMAGED.value,
    ParcelState.EXPIRED.value,
    ParcelState.CUSTOMER_REFUSED.value,
    ParcelState.RETURN_PENDING.value,
    ParcelState.RETURN_READY.value,
}

# Authorized transitions. `from` is the set of source states accepted by the
# atomic update; any other source state yields a controlled 409 conflict.
TRANSITIONS = {
    "RECEIVE": {"from": {ParcelState.IN_TRANSIT.value}, "to": ParcelState.AVAILABLE_FOR_PICKUP.value},
    "AUTHORIZE": {"from": {ParcelState.AVAILABLE_FOR_PICKUP.value}, "to": ParcelState.PICKUP_AUTHORIZED.value},
    "COMPLETE": {"from": {ParcelState.PICKUP_AUTHORIZED.value}, "to": ParcelState.HANDED_OVER.value},
    "EXPIRE": {"from": {ParcelState.AVAILABLE_FOR_PICKUP.value}, "to": ParcelState.EXPIRED.value},
    "REFUSE": {"from": {ParcelState.AVAILABLE_FOR_PICKUP.value}, "to": ParcelState.CUSTOMER_REFUSED.value},
    "DAMAGE": {
        "from": {ParcelState.AVAILABLE_FOR_PICKUP.value, ParcelState.PICKUP_AUTHORIZED.value},
        "to": ParcelState.DAMAGED.value,
    },
    "RETURN_CREATE": {
        "from": {
            ParcelState.EXPIRED.value,
            ParcelState.CUSTOMER_REFUSED.value,
            ParcelState.AVAILABLE_FOR_PICKUP.value,
            ParcelState.DAMAGED.value,
        },
        "to": ParcelState.RETURN_PENDING.value,
    },
    "RETURN_READY": {"from": {ParcelState.RETURN_PENDING.value}, "to": ParcelState.RETURN_READY.value},
    "RETURN_COLLECT": {
        "from": {ParcelState.RETURN_READY.value, ParcelState.RETURN_PENDING.value},
        "to": ParcelState.RETURN_COLLECTED.value,
    },
    "RETURN_COMPLETE": {
        "from": {ParcelState.RETURN_COLLECTED.value, ParcelState.RETURN_IN_TRANSIT.value},
        "to": ParcelState.RETURN_RECEIVED.value,
    },
}


def allowed_actions(state: str, past_deadline: bool = False) -> list[str]:
    """UI helper: contextual actions a partner may take from a given state."""
    s = state
    if s == ParcelState.IN_TRANSIT.value:
        return ["RECEIVE"]
    if s == ParcelState.AVAILABLE_FOR_PICKUP.value:
        actions = ["HANDOVER", "REFUSE", "INCIDENT"]
        if past_deadline:
            actions.insert(1, "EXPIRE")
        return actions
    if s == ParcelState.PICKUP_AUTHORIZED.value:
        return ["COMPLETE", "INCIDENT"]
    if s in (ParcelState.EXPIRED.value, ParcelState.CUSTOMER_REFUSED.value, ParcelState.DAMAGED.value):
        return ["RETURN_CREATE", "INCIDENT"]
    if s == ParcelState.RETURN_PENDING.value:
        return ["RETURN_READY", "INCIDENT"]
    if s == ParcelState.RETURN_READY.value:
        return ["INCIDENT"]
    return []
