"""Errors exposed by a knowledge substrate adapter."""


class SubstrateError(RuntimeError):
    pass


class SubstrateConfigurationError(SubstrateError):
    pass


class SubstrateRequestError(SubstrateError):
    def __init__(self, status: int, operation: str):
        self.status = int(status)
        self.operation = operation
        super().__init__(f"Knowledge substrate {operation} failed with HTTP {status}.")
