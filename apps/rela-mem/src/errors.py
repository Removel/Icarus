"""HTTP-independent errors shared across application layers."""


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def not_found(entity: str) -> ServiceError:
    return ServiceError("NOT_FOUND", f"{entity} not found", 404)
