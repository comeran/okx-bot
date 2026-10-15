from __future__ import annotations


class MalformedProviderData(ValueError):
    """Provider data cannot be safely mapped to an internal snapshot."""


class LiveConfigurationError(ValueError):
    """Live exchange configuration cannot create an adapter."""


class AcceptedUnknownOrderResultError(RuntimeError):
    """Order submission may have been accepted but has no reliable result identity."""


AcceptedUnknownOrderError = AcceptedUnknownOrderResultError
ReconciliationRequiredError = AcceptedUnknownOrderResultError
